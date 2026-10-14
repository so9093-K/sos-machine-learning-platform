"""PostgreSQL 테이블 정의. Alembic 마이그레이션의 기준 스키마다."""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

datasets = Table(
    "datasets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", Text, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("location", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("dataset_id", String(36), ForeignKey("datasets.id"), nullable=False),
    Column("spec", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("result", Text),
    Column("error", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

attempts = Table(
    "attempts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("error", Text),
    UniqueConstraint("job_id", "number"),
)

workers = Table(
    "workers",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("heartbeat_at", Text, nullable=False),
    Column("active", Boolean, nullable=False),
)
