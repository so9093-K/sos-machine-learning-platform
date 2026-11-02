"""PostgreSQL 테이블 정의. Alembic 마이그레이션의 기준 스키마다."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
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
    Column("row_count", BigInteger),
    Column("created_at", Text, nullable=False),
    Column("owner_actor", Text),
    Column("source_job_id", String(36), ForeignKey("jobs.id"), nullable=True),
    Index("ix_datasets_source_job", "source_job_id"),
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
    Column("dataset_id", String(36), ForeignKey("datasets.id"), nullable=True),
    Column("spec", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("cancel_requested", Boolean, nullable=False, server_default=text("false")),
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

# Worker의 대기 Job 조회. 완료된 실행 기록이 쌓여도 Queued Job만 읽는다.
Index("ix_jobs_queued_created", jobs.c.created_at, postgresql_where=text("status = 'Queued'"))
Index(
    "ix_jobs_playground_model_actor_created",
    text("((spec::jsonb ->> 'model_revision_id'))"),
    jobs.c.owner_actor, jobs.c.created_at.desc(), jobs.c.id.desc(),
    postgresql_where=text("kind = 'ml_playground'"),
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
    # SOS 학습 판본은 학습 Dataset을, MLflow에서 연결한 판본은 출처 기록(source)을 갖는다.
    Column("dataset_id", String(36), ForeignKey("datasets.id")),
    Column("dataset_version", Text),
    Column("algorithm", String(64), nullable=False),
    Column("source_kind", String(32), nullable=False),
    Column("source", Text),
    Column("location", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("features", Text, nullable=False),
    Column("params", Text, nullable=False),
    Column("training_score_summary", Text, nullable=False),
    Column("runtime", Text, nullable=False),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Column("registry_key", String(40), nullable=False, server_default="default"),
    Column("lifecycle_state", String(16), nullable=False, server_default="Active"),
    # SOS 모델 그룹에 대한 MLflow 등록 결과(외부 기록). 사용 판정에는 쓰지 않는다.
    Column("registry_model", Text),
    Column("registry_version", String(32)),
    # 판본 보관. 신규 사용을 막는 SOS의 결정이며 아티팩트 삭제가 아니다.
    Column("archived_at", Text),
    CheckConstraint(
        "(source_kind = 'sos_training' AND dataset_id IS NOT NULL AND dataset_version IS NOT NULL "
        "AND source IS NULL) OR "
        "(source_kind = 'mlflow_registry' AND dataset_id IS NULL AND dataset_version IS NULL "
        "AND source IS NOT NULL)",
        name="ck_model_revisions_source",
    ),
    CheckConstraint(
        "lifecycle_state IN ('Active','Deleting','Deleted')", name="ck_model_lifecycle_state"
    ),
    Index("ix_model_revisions_owner_created", "owner_actor", "created_at"),
)

# 소유 Actor별 목적 모델 그룹. 이름·설명·보관은 SOS가 정본이며 MLflow에 반영하지 않는다.
model_groups = Table(
    "model_groups",
    metadata,
    Column("owner_actor", Text, primary_key=True),
    Column("registry_key", String(40), primary_key=True),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("archived_at", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

# 소유자가 다른 Actor에게 명시적으로 제공한 모델 판본. 학습 데이터 접근 권한은 포함하지 않는다.
model_offerings = Table(
    "model_offerings",
    metadata,
    Column(
        "model_revision_id", String(36), ForeignKey("model_revisions.id"), primary_key=True
    ),
    Column("description", Text, nullable=False),
    Column("offered_by", Text, nullable=False),
    Column("offered_at", Text, nullable=False),
    Column("withdrawn_by", Text),
    Column("withdrawn_at", Text),
    Index("ix_model_offerings_withdrawn_offered", "withdrawn_at", "offered_at"),
)

# 온라인 서빙의 배포 의도. 실제 실행 상태는 Ray Serve가 정본이므로 저장하지 않는다.
# Endpoint는 외부 소비자의 안정 식별자이고, 판본 전환은 준비가 확인된 뒤 활성 판본을 바꾼다.
serving_endpoints = Table(
    "serving_endpoints",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", Text, nullable=False),
    Column("owner_actor", Text, nullable=False),
    Column("owner_actor_type", Text, nullable=False),
    Column("desired_state", Text, nullable=False),
    Column("active_model_revision_id", String(36), ForeignKey("model_revisions.id")),
    Column("pending_model_revision_id", String(36), ForeignKey("model_revisions.id")),
    Column("previous_model_revision_id", String(36), ForeignKey("model_revisions.id")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    UniqueConstraint("owner_actor", "name"),
    CheckConstraint("desired_state IN ('serving','stopped')", name="ck_serving_endpoints_state"),
    CheckConstraint(
        "active_model_revision_id IS NOT NULL OR pending_model_revision_id IS NOT NULL",
        name="ck_serving_endpoints_revision",
    ),
    CheckConstraint(
        "pending_model_revision_id IS NULL "
        "OR pending_model_revision_id IS DISTINCT FROM active_model_revision_id",
        name="ck_serving_endpoints_pending",
    ),
    Index("ix_serving_endpoints_active", "active_model_revision_id"),
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

# Scoring 결과의 점수순 관측값 색인 조각. order는 점수 순위, position은 결과 행 번호 기준이다.
scoring_observation_parts = Table(
    "scoring_observation_parts",
    metadata,
    Column("scoring_result_id", String(36), ForeignKey("scoring_results.id"), primary_key=True),
    Column("kind", String(16), primary_key=True),
    Column("part", Integer, primary_key=True),
    Column("first_position", BigInteger, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("location", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    CheckConstraint("kind IN ('order','position')", name="ck_scoring_observation_parts_kind"),
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
    Column("scoring_result_id", String(36), ForeignKey("scoring_results.id"), nullable=True),
    Column("playground_job_id", String(36), ForeignKey("jobs.id"), nullable=True),
    Column("model_revision_id", String(36), ForeignKey("model_revisions.id"), nullable=False),
    Column("row_index", Integer, nullable=True),
    Column("method", String(96), nullable=False),
    Column("output", Text, nullable=False),
    Column("owner_actor", Text),
    Column("created_at", Text, nullable=False),
    Index("ix_explanations_scoring_row", "scoring_result_id", "row_index"),
    Index("ix_explanations_playground", "playground_job_id"),
    CheckConstraint(
        "(scoring_result_id IS NOT NULL AND row_index IS NOT NULL AND playground_job_id IS NULL) "
        "OR (scoring_result_id IS NULL AND row_index IS NULL AND playground_job_id IS NOT NULL)",
        name="ck_explanation_source_exactly_one",
    ),
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
