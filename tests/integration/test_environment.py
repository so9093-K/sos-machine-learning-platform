"""통합 테스트 환경 실패 통합 계약을 검증한다."""

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from sos_platform.config import Settings


def test_test_fixture_releases_created_database_on_migration_failure(tmp_path, monkeypatch):
    """Fixture 준비 중 Alembic이 실패해도 생성된 DB는 남기지 않는다."""
    base = make_url(Settings.from_env().database_url)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")

    def databases():
        with admin.connect() as conn:
            return set(
                conn.execute(
                    text("SELECT datname FROM pg_database WHERE datname LIKE 'sos_test_%'")
                ).scalars()
            )

    try:
        before = databases()

        def fail_upgrade(*_args, **_kwargs):
            raise RuntimeError("Simulated migration setup failure")

        monkeypatch.setattr(command, "upgrade", fail_upgrade)
        fixture = workspace.__wrapped__(tmp_path, monkeypatch)
        with pytest.raises(RuntimeError, match="Simulated migration setup failure"):
            next(fixture)
        assert databases() == before
    finally:
        admin.dispose()
