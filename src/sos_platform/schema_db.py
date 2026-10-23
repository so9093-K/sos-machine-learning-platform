"""PostgreSQL 테이블 정의. Alembic 마이그레이션의 기준 스키마다."""

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
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
    Column("parse_options", Text),
    Column("created_at", Text, nullable=False),
    Column("owner_actor", Text),
    Index("ix_datasets_created_at_id", "created_at", "id"),
    Index("ix_datasets_owner_created_at_id", "owner_actor", "created_at", "id"),
)

iceberg_table_owners = Table(
    "iceberg_table_owners",
    metadata,
    Column("table_name", Text, primary_key=True),
    Column("owner_actor", Text, nullable=False),
    Column("token", String(36), nullable=False),
    Column("table_uuid", String(36)),
    Column("adoption_dataset_id", String(36)),
    Column("state", String(16), nullable=False),
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
    Column("error_code", String(64)),
    Column("failure_stage", String(64)),
    Column("failure_category", String(64)),
    Column("exception_type", String(64)),
    Column("request_id", String(36)),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("owner_actor", Text),
    Index("ix_jobs_created_at_id", "created_at", "id"),
    Index("ix_jobs_dataset_created_id", "dataset_id", "created_at", "id"),
    Index("ix_jobs_request_id", "request_id"),
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
    Column("error_code", String(64)),
    Column("failure_stage", String(64)),
    Column("failure_category", String(64)),
    Column("exception_type", String(64)),
    UniqueConstraint("job_id", "number"),
)

workers = Table(
    "workers",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("heartbeat_at", Text, nullable=False),
    Column("active", Boolean, nullable=False),
)

# API 다중 인스턴스에서 Job 제출을 원자적으로 고정하는 신원.
job_submissions = Table(
    "job_submissions",
    metadata,
    Column("actor_id", String(128), primary_key=True),
    Column("submission_key", String(128), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=True, unique=True),
    Column("created_at", Text, nullable=False),
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
    Column("fingerprint", Text),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("observation_count", Integer, nullable=False),
)

# Snapshot Tag는 외부 Catalog 트랜잭션이므로 Dataset 게시보다 먼저 의도를 기록한다.
snapshot_retention = Table(
    "snapshot_retention",
    metadata,
    Column("dataset_id", String(36), primary_key=True),
    Column("location", Text, nullable=False),
    Column("snapshot_id", Text, nullable=False),
    Column("tag_name", Text, nullable=False, unique=True),
    Column("state", String(20), nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("error", Text),
)

dataset_profiles = Table(
    "dataset_profiles",
    metadata,
    Column("job_id", String(36), ForeignKey("jobs.id"), primary_key=True),
    Column("dataset_id", String(36), ForeignKey("datasets.id"), nullable=False),
    Column("dataset_version", Text, nullable=False),
    Column("scope", String(16), nullable=False),
    Column("result", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Index("ix_dataset_profiles_dataset_created", "dataset_id", "created_at", "job_id"),
)

instance_settings = Table(
    "instance_settings",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
)

model_revisions = Table(
    "model_revisions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("attempt_id", String(36), ForeignKey("attempts.id"), nullable=False, unique=True),
    Column("dataset_id", String(36), ForeignKey("datasets.id"), nullable=False),
    Column("dataset_version", Text, nullable=False),
    Column("algorithm", String(64), nullable=False),
    Column("location", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("features", Text, nullable=False),
    Column("params", Text, nullable=False),
    Column("training_score_summary", Text, nullable=False),
    Column("runtime", Text, nullable=False),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Column("registry_model", Text),
    Column("registry_version", String(32)),
    Index("ix_model_revisions_owner_created", "owner_actor", "created_at"),
)

scoring_results = Table(
    "scoring_results",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("attempt_id", String(36), ForeignKey("attempts.id"), nullable=False, unique=True),
    Column("model_revision_id", String(36), ForeignKey("model_revisions.id"), nullable=False),
    Column("dataset_id", String(36), ForeignKey("datasets.id"), nullable=False),
    Column("dataset_version", Text, nullable=False),
    Column("location", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("score_direction", String(40), nullable=False),
    Column("feature_names", Text, nullable=False),
    Column("score_summary", Text, nullable=False),
    Column("ranking_metrics", Text),
    Column("label_column", Text),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Index("ix_scoring_results_model_created", "model_revision_id", "created_at"),
)

decision_policy_revisions = Table(
    "decision_policy_revisions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("attempt_id", String(36), ForeignKey("attempts.id"), nullable=False, unique=True),
    Column("scoring_result_id", String(36), ForeignKey("scoring_results.id"), nullable=False),
    Column("mode", String(20), nullable=False),
    Column("threshold", Float, nullable=False),
    Column("alert_fraction", Float),
    Column("decision_summary", Text, nullable=False),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Index("ix_ml_policy_scoring", "scoring_result_id"),
)

evaluation_revisions = Table(
    "evaluation_revisions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("attempt_id", String(36), ForeignKey("attempts.id"), nullable=False, unique=True),
    Column("scoring_result_id", String(36), ForeignKey("scoring_results.id"), nullable=False),
    Column("policy_revision_id", String(36), ForeignKey("decision_policy_revisions.id")),
    Column("label_coverage", String(16), nullable=False),
    Column("decision_metrics", Text, nullable=False),
    Column("ranking_metrics", Text),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Index("ix_ml_evaluation_scoring", "scoring_result_id"),
)

explanation_results = Table(
    "explanation_results",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("attempt_id", String(36), ForeignKey("attempts.id"), nullable=False, unique=True),
    Column("scoring_result_id", String(36), ForeignKey("scoring_results.id"), nullable=False),
    Column("model_revision_id", String(36), ForeignKey("model_revisions.id"), nullable=False),
    Column("row_index", Integer, nullable=False),
    Column("method", String(96), nullable=False),
    Column("output", Text, nullable=False),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Index("ix_explanations_scoring_row", "scoring_result_id", "row_index"),
)

mlflow_exports = Table(
    "mlflow_exports",
    metadata,
    Column("job_id", String(36), ForeignKey("jobs.id"), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("run_id", String(128)),
    Column("destination_fingerprint", String(64)),
    Column("attempts", Integer, nullable=False),
    Column("last_error_code", String(64)),
    Column("retry_after", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Index("ix_mlflow_exports_due", "state", "retry_after"),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("definition_id", String(96), nullable=False),
    Column("definition_version", String(32), nullable=False),
    Column("dataset_id", String(36), ForeignKey("datasets.id"), nullable=False),
    Column("dataset_version", Text, nullable=False),
    Column("owner_actor", Text, nullable=False),
    Column("owner_actor_type", String(16), nullable=False),
    Column("input_plan", Text, nullable=False),
    Column("stage_index", Integer, nullable=False),
    Column("job_ids", Text, nullable=False),
    Column("status", String(24), nullable=False),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("error_code", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Index("ix_pipeline_run_owner_created", "owner_actor", "created_at"),
)

pipeline_submissions = Table(
    "pipeline_submissions",
    metadata,
    Column("actor_id", Text, primary_key=True),
    Column("submission_key", String(128), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("run_id", String(36), ForeignKey("pipeline_runs.id", deferrable=True, initially="DEFERRED"), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("at", Text, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("actor_type", String(24), nullable=False),
    Column("action", String(96), nullable=False),
    Column("resource_type", String(40), nullable=False),
    Column("resource_id", Text),
    Column("decision", String(12), nullable=False),
    Column("outcome", String(20), nullable=False),
    Column("request_id", String(36)),
    Column("reason", String(200)),
    Index("ix_audit_events_at_id", "at", "id"),
    Index("ix_audit_events_request_id", "request_id"),
    Index("ix_audit_events_resource_id", "resource_id"),
)
