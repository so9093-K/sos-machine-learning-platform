"""애플리케이션과 동일한 설정으로 스키마 마이그레이션을 수행한다."""

from alembic import context
from sqlalchemy import create_engine, pool

from sos_platform.config import Settings
from sos_platform.schema_db import metadata

config = context.config
target_metadata = metadata


def include_name(name, type_, parent_names):
    """Alembic이 소유하지 않는 Iceberg Catalog 테이블은 비교 대상에서 제외한다."""
    if type_ == "table":
        return parent_names["schema_qualified_table_name"] in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or Settings.from_env().database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or Settings.from_env().database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_name=include_name
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
