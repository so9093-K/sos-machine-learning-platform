"""플랫폼 메타데이터와 Job 실행 이력을 PostgreSQL에 보관한다."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

from .config import Settings


def now() -> str:
    return datetime.now(UTC).isoformat()


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
        """초기화는 DB 연결·스키마 호환성을 확인하고 자동 생성하지 않는다."""
        with self.connection() as db:
            db.execute(text("SELECT 1"))
            version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if version != "0001":
                raise RuntimeError("Database migration is required: alembic upgrade head")

    def register_dataset(self, *, name: str, kind: str, location: str, version: str) -> dict:
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
        return item

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM datasets WHERE id=:id"), {"id": dataset_id})
                .mappings()
                .first()
            )
            return dict(row) if row else None

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

    def get_job(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id})
                .mappings()
                .first()
            )
            if not row:
                return None
            item = dict(row)
            item["spec"] = json.loads(item["spec"])
            item["result"] = json.loads(item["result"]) if item["result"] else None
            item["cancel_requested"] = bool(item["cancel_requested"])
            item["attempts"] = [
                dict(a)
                for a in db.execute(
                    text("SELECT * FROM attempts WHERE job_id=:id ORDER BY number"), {"id": job_id}
                ).mappings()
            ]
            return item

    def jobs(self) -> list[dict]:
        with self.connection() as db:
            ids = [r[0] for r in db.execute(text("SELECT id FROM jobs ORDER BY created_at DESC"))]
        return [self.get_job(id_) for id_ in ids]

    def request_cancel(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT status FROM jobs WHERE id=:id FOR UPDATE"), {"id": job_id})
                .mappings()
                .first()
            )
            if row and row["status"] == "Queued":
                db.execute(
                    text("UPDATE jobs SET status='Cancelled',updated_at=:at WHERE id=:id"),
                    {"at": now(), "id": job_id},
                )
            elif row and row["status"] == "Running":
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
                    text("SELECT id,cancel_requested,kind FROM jobs WHERE status='Running' FOR UPDATE")
                )
                .mappings()
                .all()
            )
            for row in rows:
                # Iceberg 적재는 Worker가 죽기 직전에 커밋됐을 가능성이 있다.
                # 자동 재시도하면 동일 데이터를 두 번 추가할 수 있으므로 명시적 확인을 요구한다.
                unsafe = row["kind"] == "iceberg_import" and not row["cancel_requested"]
                status = ("Cancelled" if row["cancel_requested"] else
                          "Failed" if unsafe else "Queued")
                message = ("Iceberg 적재가 중단됐습니다. Snapshot을 확인한 뒤 재실행하세요."
                           if unsafe else None)
                db.execute(
                    text("UPDATE jobs SET status=:s,error=:error,updated_at=:at WHERE id=:id"),
                    {"s": status, "error": message, "at": now(), "id": row["id"]},
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
