"""플랫폼 메타데이터와 Job 실행 이력을 PostgreSQL에 보관한다."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, create_engine, text

from .config import Settings


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
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
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

    def observe_objects(self, locations: list[str], observation_hours: int) -> dict:
        """S3 읽기 전용 대조의 관측 시각을 기록하고 참조·소유권을 함께 조회한다."""
        if not locations:
            return {}
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
            for location in locations:
                db.execute(
                    text(
                        "INSERT INTO object_observations"
                        "(location,first_seen_at,last_seen_at,observation_count) "
                        "VALUES(:location,:at,:at,1) ON CONFLICT(location) DO UPDATE "
                        "SET last_seen_at=:at,observation_count=object_observations.observation_count+1 "
                        "WHERE object_observations.last_seen_at<=:cutoff"
                    ),
                    {"location": location, "at": now(), "cutoff": cutoff},
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

    def register_dataset(
        self, *, name: str, kind: str, location: str, version: str, write_id: str | None = None
    ) -> dict:
        item = dict(
            id=str(uuid4()),
            name=name,
            kind=kind,
            location=location,
            version=str(version),
            created_at=now(),
        )
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO datasets(id,name,kind,location,version,created_at) "
                    "VALUES(:id,:name,:kind,:location,:version,:created_at)"
                ),
                item,
            )
            if write_id:
                self._publish_object(db, write_id, location, version)
        return item

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM datasets WHERE id=:id"), {"id": dataset_id})
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def dataset_location_referenced(self, location: str) -> bool:
        """결과 게시 실패 시 다른 Dataset 참조를 보존하기 위한 확인."""
        with self.connection() as db:
            return (
                db.execute(
                    text("SELECT 1 FROM datasets WHERE location=:location LIMIT 1"),
                    {"location": location},
                ).first()
                is not None
            )

    def datasets(self) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text("SELECT * FROM datasets ORDER BY created_at DESC")
                ).mappings()
            ]

    def create_job(self, *, kind: str, dataset_id: str, spec: dict) -> dict:
        item = dict(
            id=str(uuid4()),
            kind=kind,
            dataset_id=dataset_id,
            spec=json.dumps(spec),
            status="Queued",
            created_at=now(),
            updated_at=now(),
        )
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO jobs(id,kind,dataset_id,spec,status,created_at,updated_at) "
                    "VALUES(:id,:kind,:dataset_id,:spec,:status,:created_at,:updated_at)"
                ),
                item,
            )
        return self.get_job(item["id"])

    @staticmethod
    def _decode_job(item: dict) -> dict:
        """Job JSON과 취소 플래그를 API 응답 계약으로 변환한다."""
        item["spec"] = json.loads(item["spec"])
        item["result"] = json.loads(item["result"]) if item["result"] else None
        item["cancel_requested"] = bool(item["cancel_requested"])
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
                dict(a)
                for a in db.execute(
                    text("SELECT * FROM attempts WHERE job_id=:id ORDER BY number"), {"id": job_id}
                ).mappings()
            ]
            return item

    def jobs(self, *, limit: int = 20, before: dict | None = None) -> list[dict]:
        """정렬키 커서를 사용해 Job 및 Attempt를 한 페이지씩 일괄 조회한다."""
        if not 1 <= limit <= 101:
            raise ValueError("Job query limit is out of range")
        predicate = "WHERE (created_at, id) < (:created_at, :id)" if before else ""
        query = text(
            f"SELECT * FROM jobs {predicate} ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        params = {"limit": limit, **(before or {})}
        with self.connection() as db:
            rows = db.execute(query, params).mappings().all()
            items = [dict(row) for row in rows]
            if not items:
                return []
            attempts = (
                db.execute(
                    text("SELECT * FROM attempts WHERE job_id IN :ids ORDER BY number").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": [item["id"] for item in items]},
                )
                .mappings()
                .all()
            )
        grouped: dict[str, list[dict]] = {item["id"]: [] for item in items}
        for attempt in attempts:
            grouped[attempt["job_id"]].append(dict(attempt))
        for item in items:
            self._decode_job(item)
            item["attempts"] = grouped[item["id"]]
        return items

    def request_cancel(self, job_id: str) -> dict | None:
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
        return self.get_job(job_id)

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
    ) -> str:
        with self.connection() as db:
            row = (
                db.execute(
                    text("SELECT status,cancel_requested FROM jobs WHERE id=:id FOR UPDATE"),
                    {"id": job_id},
                )
                .mappings()
                .first()
            )
            if not row or row["status"] != "Running":
                raise RuntimeError("Job is not running")
            if row["cancel_requested"]:
                status, result, publish = "Cancelled", None, None
            if status == "Succeeded" and publish:
                item = dict(
                    id=str(uuid4()),
                    name=publish["name"],
                    kind=publish.get("kind", "file"),
                    location=publish["location"],
                    version=publish["version"],
                    created_at=now(),
                )
                db.execute(
                    text(
                        "INSERT INTO datasets(id,name,kind,location,version,created_at) "
                        "VALUES(:id,:name,:kind,:location,:version,:created_at)"
                    ),
                    item,
                )
                if write_id:
                    self._publish_object(db, write_id, publish["location"], publish["version"])
                result = {**(result or {}), "dataset_id": item["id"]}
            db.execute(
                text(
                    "UPDATE jobs SET status=:status,result=:result,error=:error,"
                    "updated_at=:at WHERE id=:id"
                ),
                dict(
                    id=job_id,
                    status=status,
                    result=json.dumps(result, default=str) if result is not None else None,
                    error=error,
                    at=now(),
                ),
            )
            db.execute(
                text(
                    "UPDATE attempts SET status=:status,finished_at=:at,error=:error "
                    "WHERE job_id=:id AND status='Running'"
                ),
                dict(id=job_id, status=status, at=now(), error=error),
            )
        return status

    def recover(self):
        """단일 Worker 소유권이 확보된 후에만 미완료 실행을 복구한다."""
        with self.connection() as db:
            rows = (
                db.execute(
                    text(
                        "SELECT id,cancel_requested,kind FROM jobs WHERE status='Running' FOR UPDATE"
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
                    text("UPDATE jobs SET status=:s,error=:error,updated_at=:at WHERE id=:id"),
                    {"s": status, "error": message, "at": now(), "id": row["id"]},
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
                db.execute(
                    text(
                        "UPDATE attempts SET status='Interrupted',finished_at=:at,error=:error "
                        "WHERE job_id=:id AND status='Running'"
                    ),
                    {"at": now(), "error": "Worker stopped before completion", "id": row["id"]},
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
