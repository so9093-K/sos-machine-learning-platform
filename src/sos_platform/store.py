"""플랫폼 메타데이터와 Job 실행 이력을 PostgreSQL에 보관한다."""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, create_engine, text

from .config import Settings
from .errors import ERRORS, PlatformError


def now() -> str:
    return datetime.now(UTC).isoformat()


class IcebergCancellationConflict(Exception):
    """외부 Iceberg 커밋의 롤백을 보장할 수 없는 실행 중 취소 요청."""


class Store:
    """데이터·작업 이력을 담당하며 실제 연산과 파일 저장은 처리하지 않는다."""

    def __init__(self, home: Path, database_url: str | None = None):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "cache").mkdir(exist_ok=True)
        (self.home / "results").mkdir(exist_ok=True)
        self.database_url = database_url or Settings.from_env().database_url
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        self._worker_lock = None

    @contextmanager
    def connection(self):
        with self.engine.begin() as connection:
            yield connection

    def initialize(self):
        """현재 Alembic HEAD와 DB 스키마가 일치하는지만 확인한다."""
        source_root = Path(__file__).resolve().parents[2]
        if (source_root / "migrations").is_dir() and (source_root / "alembic.ini").is_file():
            # 소스 체크아웃과 wheel 설치의 경로가 같을 필요는 없다.
            config_file = source_root / "alembic.ini"
            scripts = source_root / "migrations"
        else:
            package_root = files("sos_platform")
            config_file = package_root.joinpath("alembic.ini")
            scripts = package_root.joinpath("migrations")
            if not config_file.is_file() or not scripts.is_dir():
                raise RuntimeError("Packaged Alembic migration resources are missing")
        config = Config(str(config_file))
        config.set_main_option("script_location", str(scripts))
        expected = set(ScriptDirectory.from_config(config).get_heads())
        with self.connection() as db:
            db.execute(text("SELECT 1"))
            actual = set(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
        if actual != expected:
            raise RuntimeError("Database migration is required: alembic upgrade head")

    def begin_object_write(self, *, location: str, owner_kind: str, owner_id: str) -> str:
        """S3에 쓰기 전에 게시 의도를 DB에 확정한다."""
        if owner_kind not in ("upload", "attempt"):
            raise ValueError("Invalid object owner")
        write_id = str(uuid4())
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO object_writes(id,location,owner_kind,owner_id,state,created_at,updated_at) "
                    "VALUES(:id,:location,:owner_kind,:owner_id,'Pending',:at,:at)"
                ),
                {
                    "id": write_id,
                    "location": location,
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "at": now(),
                },
            )
        return write_id

    def fail_object_write(self, write_id: str, error: str | None = None) -> None:
        """성공 게시된 객체는 실패로 되돌리지 않는다."""
        with self.connection() as db:
            db.execute(
                text(
                    "UPDATE object_writes SET state='Failed',error=:error,updated_at=:at "
                    "WHERE id=:id AND state='Pending'"
                ),
                {"id": write_id, "error": error[:500] if error else None, "at": now()},
            )

    @staticmethod
    def _publish_object(db, write_id: str, location: str, version: str) -> None:
        updated = db.execute(
            text(
                "UPDATE object_writes SET state='Published',version=:version,updated_at=:at "
                "WHERE id=:id AND location=:location AND state='Pending'"
            ),
            {"id": write_id, "location": location, "version": version, "at": now()},
        )
        if updated.rowcount != 1:
            raise RuntimeError("Object publication intent is not pending")

    def object_writes(self, location: str) -> list[dict]:
        """진단에서 게시 기록을 조회한다."""
        with self.connection() as db:
            return [
                dict(item)
                for item in db.execute(
                    text(
                        "SELECT * FROM object_writes WHERE location=:location ORDER BY created_at"
                    ),
                    {"location": location},
                ).mappings()
            ]

    def observe_objects(self, objects: list[dict], observation_hours: int) -> dict:
        """객체 세대가 변경되면 이전 관측 횟수를 폐기하고 새로 집계한다.

        목록 ETag는 멀티파트 등에서 콘텐츠 해시가 아닐 수 있다. 따라서
        ETag·수정시각·크기를 결합한 값은 삭제 증거가 아닌 진단 단서다.
        """
        if not objects:
            return {}
        locations = [item["location"] for item in objects]
        cutoff = (datetime.now(UTC) - timedelta(hours=observation_hours)).isoformat()
        statement = text("SELECT location FROM datasets WHERE location IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        writes_query = text("SELECT * FROM object_writes WHERE location IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        with self.connection() as db:
            referenced = set(db.execute(statement, {"ids": locations}).scalars())
            writes = db.execute(writes_query, {"ids": locations}).mappings().all()
            by_location: dict[str, list[dict]] = {location: [] for location in locations}
            for write in writes:
                by_location[write["location"]].append(dict(write))
            owner_ids = [item["owner_id"] for item in writes if item["owner_kind"] == "attempt"]
            attempts = {}
            if owner_ids:
                statement = text("SELECT id,status FROM attempts WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                )
                attempts = dict(db.execute(statement, {"ids": owner_ids}).all())
            # 모든 객체에 동일한 관측 시각을 적용하고 한 번의 executemany로 보낸다.
            at = now()
            db.execute(
                text(
                    "INSERT INTO object_observations"
                    "(location,fingerprint,first_seen_at,last_seen_at,observation_count) "
                    "VALUES(:location,:fingerprint,:at,:at,1) "
                    "ON CONFLICT(location) DO UPDATE SET "
                    "fingerprint=excluded.fingerprint,"
                    "first_seen_at=CASE WHEN object_observations.fingerprint "
                    "IS DISTINCT FROM excluded.fingerprint THEN excluded.first_seen_at "
                    "ELSE object_observations.first_seen_at END,"
                    "observation_count=CASE WHEN object_observations.fingerprint "
                    "IS DISTINCT FROM excluded.fingerprint THEN 1 "
                    "WHEN object_observations.last_seen_at<=:cutoff "
                    "THEN object_observations.observation_count+1 "
                    "ELSE object_observations.observation_count END,"
                    "last_seen_at=CASE WHEN object_observations.fingerprint "
                    "IS DISTINCT FROM excluded.fingerprint "
                    "OR object_observations.last_seen_at<=:cutoff "
                    "THEN excluded.last_seen_at ELSE object_observations.last_seen_at END"
                ),
                [
                    {
                        "location": item["location"],
                        "fingerprint": item.get("fingerprint"),
                        "at": at,
                        "cutoff": cutoff,
                    }
                    for item in objects
                ],
            )
            observations_query = text(
                "SELECT * FROM object_observations WHERE location IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            observations = {
                item["location"]: dict(item)
                for item in db.execute(observations_query, {"ids": locations}).mappings()
            }
        return {
            location: {
                "referenced": location in referenced,
                "writes": by_location[location],
                "attempts": attempts,
                "observation": observations[location],
            }
            for location in locations
        }

    @staticmethod
    def _audit(
        db,
        *,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        decision: str = "allow",
        outcome: str = "succeeded",
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """감사 기록이 실패하면 동일 PostgreSQL 트랜잭션의 변경도 실패한다."""
        db.execute(
            text(
                "INSERT INTO audit_events"
                "(id,at,actor_id,actor_type,action,resource_type,resource_id,"
                "decision,outcome,reason,request_id) "
                "VALUES(:id,:at,:actor_id,:actor_type,:action,:resource_type,"
                ":resource_id,:decision,:outcome,:reason,:request_id)"
            ),
            {
                "id": str(uuid4()),
                "at": now(),
                "actor_id": actor_id,
                "actor_type": actor_type,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "decision": decision,
                "outcome": outcome,
                "reason": reason,
                "request_id": request_id,
            },
        )

    def record_audit(
        self,
        *,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        decision: str = "deny",
        outcome: str = "denied",
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        with self.connection() as db:
            self._audit(
                db,
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                decision=decision,
                outcome=outcome,
                reason=reason,
                request_id=request_id,
            )

    def audit_events(
        self,
        *,
        limit: int = 50,
        request_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict]:
        """운영자만 조회하는 감사 이력을 요청·작업 식별자로 제한한다."""
        if not 1 <= limit <= 100:
            raise ValueError("Audit limit out of range")
        clauses = []
        params = {"limit": limit}
        if request_id is not None:
            clauses.append("request_id=:request_id")
            params["request_id"] = request_id
        if resource_id is not None:
            clauses.append("resource_id=:resource_id")
            params["resource_id"] = resource_id
        predicate = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(
                        f"SELECT * FROM audit_events {predicate} "
                        "ORDER BY at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                ).mappings()
            ]

    def begin_snapshot_retention(
        self, *, dataset_id: str, location: str, snapshot_id: str, tag_name: str
    ) -> None:
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO snapshot_retention"
                    "(dataset_id,location,snapshot_id,tag_name,state,created_at,updated_at) "
                    "VALUES(:dataset_id,:location,:snapshot_id,:tag_name,'Pending',:at,:at) "
                    "ON CONFLICT(dataset_id) DO NOTHING"
                ),
                {
                    "dataset_id": dataset_id,
                    "location": location,
                    "snapshot_id": snapshot_id,
                    "tag_name": tag_name,
                    "at": now(),
                },
            )
            row = (
                db.execute(
                    text(
                        "SELECT location,snapshot_id,tag_name,state FROM snapshot_retention "
                        "WHERE dataset_id=:id FOR UPDATE"
                    ),
                    {"id": dataset_id},
                )
                .mappings()
                .one()
            )
            if (
                row["location"] != location
                or row["snapshot_id"] != snapshot_id
                or row["tag_name"] != tag_name
            ):
                raise RuntimeError("Snapshot reservation mismatch")

    def fail_snapshot_retention(self, dataset_id: str, error: str) -> None:
        with self.connection() as db:
            db.execute(
                text(
                    "UPDATE snapshot_retention SET state='Uncertain',error=:error,"
                    "updated_at=:at WHERE dataset_id=:id AND state='Pending'"
                ),
                {"id": dataset_id, "error": error[:200], "at": now()},
            )

    def snapshot_retention(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            result = (
                db.execute(
                    text("SELECT * FROM snapshot_retention WHERE dataset_id=:id"),
                    {"id": dataset_id},
                )
                .mappings()
                .first()
            )
            return dict(result) if result else None

    def unpublished_snapshot_retention(self, *, limit: int = 50) -> list[dict]:
        """Dataset 게시에 실패한 보존 의도를 조회한다. 참조·Tag를 변경하지 않는다."""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(
                        "SELECT r.* FROM snapshot_retention r "
                        "LEFT JOIN datasets d ON d.id=r.dataset_id "
                        "WHERE d.id IS NULL ORDER BY r.created_at DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                ).mappings()
            ]

    @staticmethod
    def _confirm_snapshot_retention(db, dataset_id: str, location: str, snapshot_id: str) -> None:
        updated = db.execute(
            text(
                "UPDATE snapshot_retention SET state='Retained',updated_at=:at,error=NULL "
                "WHERE dataset_id=:id AND location=:location AND snapshot_id=:snapshot_id "
                "AND state IN ('Pending','Uncertain','Retained')"
            ),
            {"id": dataset_id, "location": location, "snapshot_id": str(snapshot_id), "at": now()},
        )
        if updated.rowcount != 1:
            raise RuntimeError("Snapshot protection intent is not ready")

    def confirm_existing_retention(
        self, dataset_id: str, *, actor_id: str, actor_type: str
    ) -> None:
        """기존 Dataset 보호 확인과 감사 기록을 하나의 DB 트랜잭션으로 확정한다."""
        with self.connection() as db:
            item = (
                db.execute(
                    text("SELECT location,version FROM datasets WHERE id=:id AND kind='iceberg'"),
                    {"id": dataset_id},
                )
                .mappings()
                .first()
            )
            if item is None:
                raise ValueError("Iceberg Dataset not found")
            self._confirm_snapshot_retention(db, dataset_id, item["location"], item["version"])
            self._audit(
                db,
                actor_id=actor_id,
                actor_type=actor_type,
                action="snapshot.retain",
                resource_type="dataset",
                resource_id=dataset_id,
            )

    def register_dataset(
        self,
        *,
        name: str,
        kind: str,
        location: str,
        version: str,
        write_id: str | None = None,
        dataset_id: str | None = None,
        owner_actor: str | None = None,
        retention_id: str | None = None,
        actor_type: str = "human",
    ) -> dict:
        item = dict(
            id=dataset_id or str(uuid4()),
            owner_actor=owner_actor,
            name=name,
            kind=kind,
            location=location,
            version=str(version),
            created_at=now(),
        )
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO datasets(id,name,kind,location,version,created_at,owner_actor) "
                    "VALUES(:id,:name,:kind,:location,:version,:created_at,:owner_actor)"
                ),
                item,
            )
            if write_id:
                self._publish_object(db, write_id, location, version)
            if retention_id:
                self._confirm_snapshot_retention(db, retention_id, location, version)
            if owner_actor:
                self._audit(
                    db,
                    actor_id=owner_actor,
                    actor_type=actor_type,
                    action="dataset.create",
                    resource_type="dataset",
                    resource_id=item["id"],
                )
        return item

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM datasets WHERE id=:id"), {"id": dataset_id})
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def datasets(self, *, actor_id: str | None = None) -> list[dict]:
        predicate = "WHERE owner_actor=:actor_id" if actor_id is not None else ""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(f"SELECT * FROM datasets {predicate} ORDER BY created_at DESC"),
                    {"actor_id": actor_id},
                ).mappings()
            ]

    def owns_iceberg_table(self, table_name: str, actor_id: str) -> bool:
        """명시적인 기존 Iceberg Dataset 소유권만 테이블 접근 근거로 사용한다."""
        with self.connection() as db:
            return bool(
                db.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM datasets "
                        "WHERE kind='iceberg' AND location=:table AND owner_actor=:actor)"
                    ),
                    {"table": f"default.{table_name}", "actor": actor_id},
                ).scalar_one()
            )

    def dataset_page(
        self, *, limit: int, before: dict | None = None, actor_id: str | None = None
    ) -> list[dict]:
        """안정적인 created_at+id 키셋으로 데이터셋을 조회한다."""
        if not 1 <= limit <= 101:
            raise ValueError("Dataset page limit is out of range")
        conditions = []
        params = {"limit": limit}
        if actor_id is not None:
            conditions.append("owner_actor=:actor_id")
            params["actor_id"] = actor_id
        if before is not None:
            conditions.append("(created_at,id) < (:created_at,:id)")
            params.update(before)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(
                        f"SELECT * FROM datasets{where} "
                        "ORDER BY created_at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                ).mappings()
            ]

    def latest_dataset_profile(self, dataset_id: str, version: str) -> dict | None:
        """현재 원본 판본에 대한 최신 완료 Profile을 조회한다."""
        with self.connection() as db:
            row = db.execute(
                text(
                    "SELECT result FROM dataset_profiles "
                    "WHERE dataset_id=:dataset_id AND dataset_version=:version "
                    "ORDER BY created_at DESC, job_id DESC LIMIT 1"
                ),
                {"dataset_id": dataset_id, "version": version},
            ).scalar_one_or_none()
        return json.loads(row) if row is not None else None

    def latest_understanding_job(self, dataset_id: str) -> dict | None:
        """프로파일 실행 중/실패 상태를 기존 Job의 정본에서 읽는다."""
        with self.connection() as db:
            job_id = db.execute(
                text(
                    "SELECT id FROM jobs WHERE dataset_id=:id AND kind='profile' "
                    "AND spec::jsonb->>'mode'='understanding' "
                    "ORDER BY created_at DESC,id DESC LIMIT 1"
                ),
                {"id": dataset_id},
            ).scalar_one_or_none()
        return self.get_job(job_id) if job_id else None

    def create_job(
        self,
        *,
        kind: str,
        dataset_id: str,
        spec: dict,
        owner_actor: str | None = None,
        actor_type: str = "human",
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """중복 제출을 PostgreSQL 유일성 제약과 같은 트랜잭션으로 처리한다."""
        if idempotency_key is not None and owner_actor is None:
            raise ValueError("Idempotent Job requires an actor")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"kind": kind, "dataset_id": dataset_id, "spec": spec},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        item = dict(
            id=str(uuid4()),
            kind=kind,
            dataset_id=dataset_id,
            owner_actor=owner_actor,
            request_id=request_id,
            spec=json.dumps(spec),
            status="Queued",
            created_at=now(),
            updated_at=now(),
        )
        with self.connection() as db:
            if idempotency_key is not None:
                # PostgreSQL 유일성 충돌은 동시 트랜잭션을 직렬화한다.
                inserted = db.execute(
                    text(
                        "INSERT INTO job_submissions(actor_id,submission_key,fingerprint,"
                        "job_id,created_at) VALUES(:actor,:key,:fingerprint,NULL,:at) "
                        "ON CONFLICT(actor_id,submission_key) DO NOTHING RETURNING actor_id"
                    ),
                    {
                        "actor": owner_actor,
                        "key": idempotency_key,
                        "fingerprint": fingerprint,
                        "at": now(),
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    previous = (
                        db.execute(
                            text(
                                "SELECT fingerprint,job_id FROM job_submissions "
                                "WHERE actor_id=:actor AND submission_key=:key"
                            ),
                            {"actor": owner_actor, "key": idempotency_key},
                        )
                        .mappings()
                        .one()
                    )
                    if previous["fingerprint"] != fingerprint:
                        raise PlatformError("SOS-JOB-IDEMPOTENCY-CONFLICT")
                    previous_id = previous["job_id"]
                    # get_job은 새 트랜잭션으로 기존 확정 Job을 조회한다.
                    return self.get_job(previous_id)
            db.execute(
                text(
                    "INSERT INTO jobs(id,kind,dataset_id,spec,status,created_at,updated_at,"
                    "owner_actor,request_id) "
                    "VALUES(:id,:kind,:dataset_id,:spec,:status,:created_at,:updated_at,"
                    ":owner_actor,:request_id)"
                ),
                item,
            )
            if idempotency_key is not None:
                db.execute(
                    text(
                        "UPDATE job_submissions SET job_id=:job "
                        "WHERE actor_id=:actor AND submission_key=:key"
                    ),
                    {"job": item["id"], "actor": owner_actor, "key": idempotency_key},
                )
            if owner_actor:
                self._audit(
                    db,
                    actor_id=owner_actor,
                    actor_type=actor_type,
                    action="job.create",
                    resource_type="job",
                    resource_id=item["id"],
                    request_id=request_id,
                )
        return self.get_job(item["id"])

    @staticmethod
    def _decode_job(item: dict) -> dict:
        """Job JSON과 취소 플래그를 API 응답 계약으로 변환한다."""
        item["spec"] = json.loads(item["spec"])
        item["result"] = json.loads(item["result"]) if item["result"] else None
        item["cancel_requested"] = bool(item["cancel_requested"])
        code = item.get("error_code")
        item["retry_policy"] = ERRORS[code].retry_policy if code in ERRORS else None
        if item.get("error") and code not in ERRORS:
            # 구버전 Job의 원문은 DB에서 보존하지만 API로 재노출하지 않는다.
            item["error"] = "이전 실행의 오류 기록입니다. 운영 진단에서 확인하세요."
        return item

    @staticmethod
    def _decode_attempt(item: dict) -> dict:
        if item.get("error") and item.get("error_code") not in ERRORS:
            item["error"] = "이전 실행의 상세 오류는 운영 기록에서 확인하세요."
        return item

    def get_job(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id})
                .mappings()
                .first()
            )
            if not row:
                return None
            item = self._decode_job(dict(row))
            item["attempts"] = [
                self._decode_attempt(dict(a))
                for a in db.execute(
                    text("SELECT * FROM attempts WHERE job_id=:id ORDER BY number"), {"id": job_id}
                ).mappings()
            ]
            return item

    def jobs(
        self,
        *,
        limit: int = 20,
        before: dict | None = None,
        actor_id: str | None = None,
        kind: str | None = None,
        exclude_kind: str | None = None,
    ) -> list[dict]:
        """목록은 종류별 Job 페이지와 각 Job의 마지막 Attempt 요약만 반환한다."""
        if not 1 <= limit <= 101:
            raise ValueError("Job query limit is out of range")
        filters = []
        if before:
            filters.append("(created_at, id) < (:created_at, :id)")
        if actor_id is not None:
            filters.append("owner_actor=:actor_id")
        if kind is not None:
            filters.append("kind=:kind")
        if exclude_kind is not None:
            filters.append("kind!=:exclude_kind")
        predicate = ("WHERE " + " AND ".join(filters)) if filters else ""
        query = text(
            f"SELECT * FROM jobs {predicate} ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        params = {
            "limit": limit,
            "actor_id": actor_id,
            "kind": kind,
            "exclude_kind": exclude_kind,
            **(before or {}),
        }
        with self.connection() as db:
            rows = db.execute(query, params).mappings().all()
            items = [dict(row) for row in rows]
            if not items:
                return []
            # Window 함수로 전송되는 Attempt를 Job당 하나로 제한한다.
            query = text(
                "SELECT * FROM (SELECT a.*, "
                "COUNT(*) OVER(PARTITION BY a.job_id) AS attempt_count,"
                "ROW_NUMBER() OVER(PARTITION BY a.job_id ORDER BY a.number DESC) AS rank "
                "FROM attempts a WHERE a.job_id IN :ids) history WHERE rank=1"
            ).bindparams(bindparam("ids", expanding=True))
            summaries = db.execute(query, {"ids": [item["id"] for item in items]}).mappings()
            latest = {row["job_id"]: self._decode_attempt(dict(row)) for row in summaries}
        for item in items:
            self._decode_job(item)
            summary = latest.get(item["id"])
            item["attempt_count"] = summary["attempt_count"] if summary else 0
            item["latest_attempt"] = (
                {
                    key: value
                    for key, value in summary.items()
                    if key not in ("rank", "attempt_count")
                }
                if summary
                else None
            )
        return items

    def request_cancel(
        self, job_id: str, *, actor_id: str | None = None, actor_type: str = "human"
    ) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(
                    text("SELECT status,kind FROM jobs WHERE id=:id FOR UPDATE"), {"id": job_id}
                )
                .mappings()
                .first()
            )
            if row and row["status"] == "Queued":
                db.execute(
                    text("UPDATE jobs SET status='Cancelled',updated_at=:at WHERE id=:id"),
                    {"at": now(), "id": job_id},
                )
            elif row and row["status"] == "Running":
                if row["kind"] == "iceberg_import":
                    raise IcebergCancellationConflict(
                        "실행 중 Iceberg 적재는 커밋 여부를 보장할 수 없어 취소할 수 없습니다. "
                        "완료 또는 중단 후 Snapshot을 확인하세요."
                    )
                db.execute(
                    text("UPDATE jobs SET cancel_requested=true,updated_at=:at WHERE id=:id"),
                    {"at": now(), "id": job_id},
                )
            if row and actor_id:
                self._audit(
                    db,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    action="job.cancel",
                    resource_type="job",
                    resource_id=job_id,
                )
        return self.get_job(job_id)

    def cancellation_requested(self, job_id: str) -> bool:
        """Worker가 전체 Job·Attempt를 읽지 않고 취소 플래그만 확인한다."""
        with self.connection() as db:
            value = db.execute(
                text("SELECT cancel_requested FROM jobs WHERE id=:id"), {"id": job_id}
            ).scalar_one_or_none()
        if value is None:
            raise RuntimeError("Job is missing during execution")
        return bool(value)

    def claim(self) -> dict | None:
        """행 잠금으로 하나의 Job을 정확히 한 실행 시도가 인수하도록 한다."""
        with self.connection() as db:
            row = (
                db.execute(
                    text(
                        "SELECT id FROM jobs WHERE status='Queued' "
                        "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            job_id = row["id"]
            number = db.execute(
                text("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE job_id=:id"),
                {"id": job_id},
            ).scalar_one()
            db.execute(
                text("UPDATE jobs SET status='Running',updated_at=:at WHERE id=:id"),
                {"at": now(), "id": job_id},
            )
            db.execute(
                text(
                    "INSERT INTO attempts(id,job_id,number,status,started_at) "
                    "VALUES(:id,:job,:number,'Running',:at)"
                ),
                {"id": str(uuid4()), "job": job_id, "number": number, "at": now()},
            )
        return self.get_job(job_id)

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
        publish: dict | None = None,
        write_id: str | None = None,
        error_code: str | None = None,
        failure_stage: str | None = None,
        failure_category: str | None = None,
        exception_type: str | None = None,
    ) -> str:
        if error_code is not None and error_code not in ERRORS:
            raise ValueError("Unregistered error code")
        with self.connection() as db:
            row = (
                db.execute(
                    text(
                        "SELECT status,cancel_requested,owner_actor,request_id,kind,dataset_id,spec "
                        "FROM jobs WHERE id=:id FOR UPDATE"
                    ),
                    {"id": job_id},
                )
                .mappings()
                .first()
            )
            if not row or row["status"] != "Running":
                raise RuntimeError("Job is not running")
            if row["cancel_requested"]:
                status, result, publish = "Cancelled", None, None
                error, error_code, failure_stage = None, None, None
                failure_category, exception_type = None, None
            if status == "Succeeded" and publish:
                item = dict(
                    id=publish.get("retention_id") or str(uuid4()),
                    name=publish["name"],
                    kind=publish.get("kind", "file"),
                    location=publish["location"],
                    version=publish["version"],
                    created_at=now(),
                    owner_actor=row["owner_actor"],
                )
                db.execute(
                    text(
                        "INSERT INTO datasets(id,name,kind,location,version,created_at,owner_actor) "
                        "VALUES(:id,:name,:kind,:location,:version,:created_at,:owner_actor)"
                    ),
                    item,
                )
                if write_id:
                    self._publish_object(db, write_id, publish["location"], publish["version"])
                if publish.get("retention_id"):
                    self._confirm_snapshot_retention(
                        db, publish["retention_id"], publish["location"], publish["version"]
                    )
                result = {**(result or {}), "dataset_id": item["id"]}
            if (
                status == "Succeeded"
                and row["kind"] == "profile"
                and json.loads(row["spec"]).get("mode") == "understanding"
                and result is not None
            ):
                db.execute(
                    text(
                        "INSERT INTO dataset_profiles"
                        "(job_id,dataset_id,dataset_version,scope,result,created_at) "
                        "VALUES(:job,:dataset,:version,:scope,:result,:at)"
                    ),
                    {
                        "job": job_id,
                        "dataset": row["dataset_id"],
                        "version": result["version"],
                        "scope": result["scope"],
                        "result": json.dumps(result, ensure_ascii=False, allow_nan=False),
                        "at": now(),
                    },
                )
            db.execute(
                text(
                    "UPDATE jobs SET status=:status,result=:result,error=:error,"
                    "error_code=:error_code,failure_stage=:failure_stage,"
                    "failure_category=:failure_category,exception_type=:exception_type,"
                    "updated_at=:at WHERE id=:id"
                ),
                dict(
                    id=job_id,
                    status=status,
                    result=json.dumps(result, default=str) if result is not None else None,
                    error=error,
                    error_code=error_code,
                    failure_stage=failure_stage,
                    failure_category=failure_category,
                    exception_type=exception_type,
                    at=now(),
                ),
            )
            completed_attempt = db.execute(
                text(
                    "UPDATE attempts SET status=:status,finished_at=:at,error=:error,"
                    "error_code=:error_code,failure_stage=:failure_stage,"
                    "failure_category=:failure_category,exception_type=:exception_type "
                    "WHERE job_id=:id AND status='Running' RETURNING id"
                ),
                dict(
                    id=job_id,
                    status=status,
                    at=now(),
                    error=error,
                    error_code=error_code,
                    failure_stage=failure_stage,
                    failure_category=failure_category,
                    exception_type=exception_type,
                ),
            ).scalar_one()
            reason = f"attempt={completed_attempt}"
            if status == "Succeeded" and publish:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="dataset.publish",
                    resource_type="dataset",
                    resource_id=item["id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            self._audit(
                db,
                actor_id="sos-worker",
                actor_type="service",
                action="job.finish",
                resource_type="job",
                resource_id=job_id,
                outcome={
                    "Succeeded": "succeeded",
                    "Failed": "failed",
                    "Cancelled": "cancelled",
                }.get(status, status.lower()),
                reason=reason,
                request_id=row["request_id"],
            )
        return status

    def recover(self):
        """단일 Worker 소유권이 확보된 후에만 미완료 실행을 복구한다."""
        with self.connection() as db:
            rows = (
                db.execute(
                    text(
                        "SELECT id,cancel_requested,kind,request_id "
                        "FROM jobs WHERE status='Running' FOR UPDATE"
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                # Iceberg 적재는 Worker가 죽기 직전에 커밋됐을 가능성이 있다.
                # 자동 재시도하면 동일 데이터를 두 번 추가할 수 있으므로 명시적 확인을 요구한다.
                unsafe = row["kind"] == "iceberg_import"
                status = (
                    "Failed" if unsafe else "Cancelled" if row["cancel_requested"] else "Queued"
                )
                message = (
                    "Iceberg 적재가 중단됐습니다. Snapshot을 확인한 뒤 재실행하세요."
                    if unsafe
                    else None
                )
                db.execute(
                    text(
                        "UPDATE jobs SET status=:s,error=:error,error_code=:code,"
                        "failure_stage=:stage,failure_category='process_exit',"
                        "exception_type=NULL,updated_at=:at WHERE id=:id"
                    ),
                    {
                        "s": status,
                        "error": message,
                        "at": now(),
                        "id": row["id"],
                        "code": "SOS-ICEBERG-UNCERTAIN" if unsafe else None,
                        "stage": "iceberg_commit" if unsafe else None,
                    },
                )
                # 이전 Worker 소유권이 종료된 실행의 미확정 S3 게시 의도를 해소한다.
                db.execute(
                    text(
                        "UPDATE object_writes SET state='Failed',updated_at=:at,"
                        "error='Worker interrupted before publication' "
                        "WHERE owner_kind='attempt' AND state='Pending' "
                        "AND owner_id IN (SELECT id FROM attempts "
                        "WHERE job_id=:id AND status='Running')"
                    ),
                    {"at": now(), "id": row["id"]},
                )
                interrupted = (
                    db.execute(
                        text(
                            "UPDATE attempts SET status='Interrupted',finished_at=:at,error=:error,"
                            "error_code='SOS-JOB-INTERRUPTED',failure_stage='worker_recovery',"
                            "failure_category='process_exit',exception_type=NULL "
                            "WHERE job_id=:id AND status='Running' RETURNING id"
                        ),
                        {"at": now(), "error": "Worker stopped before completion", "id": row["id"]},
                    )
                    .scalars()
                    .all()
                )
                attempt_id = interrupted[-1] if interrupted else "unknown"
                if unsafe:
                    # 자동 재시도 대신 Catalog 속성으로 커밋 여부를 추적할 근거를 남긴다.
                    db.execute(
                        text("UPDATE jobs SET error=:error WHERE id=:id"),
                        {
                            "id": row["id"],
                            "error": (
                                f"Iceberg 적재 중단: Attempt {attempt_id}의 "
                                "sos.attempt-id를 Catalog Snapshot과 대조하세요. "
                                "확인 전 다시 적재하지 마세요."
                            ),
                        },
                    )
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="job.recover",
                    resource_type="job",
                    resource_id=row["id"],
                    outcome={
                        "Queued": "requeued",
                        "Cancelled": "cancelled",
                        "Failed": "interrupted",
                    }[status],
                    reason=f"kind={row['kind']};attempt={attempt_id}"[:200],
                    request_id=row["request_id"],
                )

    def acquire_worker(self) -> bool:
        """PostgreSQL 세션 잠금으로 복구 수행자와 Worker를 단일화한다."""
        if self._worker_lock is not None:
            return True
        conn = self.engine.connect()
        try:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext('sos-data-worker'))")
            ).scalar_one()
            conn.commit()
            if acquired:
                self._worker_lock = conn
                return True
        except Exception:
            conn.close()
            raise
        conn.close()
        return False

    def heartbeat(self, *, active: bool = True):
        # Worker 소유권에 사용 중인 DB 세션이 끊겼다면 실행을 계속하지 않는다.
        if self._worker_lock is not None:
            self._worker_lock.execute(text("SELECT 1"))
            self._worker_lock.commit()
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO workers(id,heartbeat_at,active) VALUES('data-worker',:at,:active) "
                    "ON CONFLICT(id) DO UPDATE SET heartbeat_at=:at,active=:active"
                ),
                {"at": now(), "active": active},
            )

    def worker_ready(self, max_age: int = 10) -> bool:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT heartbeat_at,active FROM workers WHERE id='data-worker'"))
                .mappings()
                .first()
            )
            return bool(
                row
                and row["active"]
                and datetime.fromisoformat(row["heartbeat_at"])
                > datetime.now(UTC) - timedelta(seconds=max_age)
            )

    def release_worker(self):
        if self._worker_lock is not None:
            try:
                self.heartbeat(active=False)
                self._worker_lock.execute(
                    text("SELECT pg_advisory_unlock(hashtext('sos-data-worker'))")
                )
            finally:
                self._worker_lock.close()
                self._worker_lock = None
