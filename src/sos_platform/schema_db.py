"""PostgreSQL 테이블 정의. Alembic 마이그레이션의 기준 스키마다."""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Index,
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

# S3 객체 자체와 PostgreSQL에서 보장하는 게시 참조는 구별한다.
object_writes = Table(
    "object_writes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("location", Text, nullable=False),
    Column("owner_kind", String(16), nullable=False),
    Column("owner_id", String(36), nullable=False),
    Column("state", String(16), nullable=False),
    Column("version", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("error", Text),
    Index("ix_object_writes_location", "location"),
    Index("ix_object_writes_owner", "owner_kind", "owner_id"),
)

object_observations = Table(
    "object_observations",
    metadata,
    Column("location", Text, primary_key=True),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("observation_count", Integer, nullable=False),
)
