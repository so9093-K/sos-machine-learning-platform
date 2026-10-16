"""플랫폼 메타데이터와 Job 실행 이력을 PostgreSQL에 보관한다."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from .config import Settings
from .store_administration import AdministrationStoreMixin, now
from .store_datasets import DatasetStoreMixin
from .store_jobs import JobStoreMixin
from .store_objects import ObjectStoreMixin
from .store_snapshots import SnapshotStoreMixin


class WorkerOwnershipLost(RuntimeError):
    """Worker 소유권을 보장하던 PostgreSQL 세션이 바뀌었거나 끊겼다."""


class Store(
    AdministrationStoreMixin,
    ObjectStoreMixin,
    SnapshotStoreMixin,
    DatasetStoreMixin,
    JobStoreMixin,
):
    """데이터·작업 이력을 담당하며 실제 연산과 파일 저장은 처리하지 않는다."""

    def __init__(self, home: Path, database_url: str | None = None):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "cache").mkdir(exist_ok=True)
        (self.home / "results").mkdir(exist_ok=True)
        self.database_url = database_url or Settings.from_env().database_url
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        self._worker_lock = None
        self._worker_backend_pid: int | None = None

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

    def acquire_worker(self) -> bool:
        """PostgreSQL 세션 잠금으로 복구 수행자와 Worker를 단일화한다."""
        if self._worker_lock is not None:
            self._assert_worker_ownership()
            return True
        conn = self.engine.connect()
        try:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext('sos-data-worker'))")
            ).scalar_one()
            conn.commit()
            if acquired:
                backend_pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
                conn.commit()
                self._worker_backend_pid = backend_pid
                self._worker_lock = conn
                return True
        except Exception:
            conn.close()
            raise
        conn.close()
        return False

    def _assert_worker_ownership(self) -> None:
        """자동 DB 재연결은 session-level advisory lock의 소유권 복원이 아니다."""
        if self._worker_lock is None:
            return
        try:
            backend_pid = self._worker_lock.execute(text("SELECT pg_backend_pid()")).scalar_one()
            self._worker_lock.commit()
        except Exception as exc:
            raise WorkerOwnershipLost("Worker advisory lock session is unavailable") from exc
        if backend_pid != self._worker_backend_pid:
            raise WorkerOwnershipLost("Worker advisory lock session has changed")

    def heartbeat(self, *, active: bool = True):
        # 접속 복구만으로 잃어버린 advisory lock을 소유했다고 주장해서는 안 된다.
        self._assert_worker_ownership()
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
                try:
                    self.heartbeat(active=False)
                except WorkerOwnershipLost:
                    # 다른 Worker가 잠금을 인수했을 수 있어 그 활성 상태를 덮어쓰지 않는다.
                    return
                self._worker_lock.execute(
                    text("SELECT pg_advisory_unlock(hashtext('sos-data-worker'))")
                )
            finally:
                self._worker_lock.close()
                self._worker_lock = None
                self._worker_backend_pid = None
