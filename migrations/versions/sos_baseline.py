"""SOS 플랫폼 스키마 기준선.

첫 릴리스 전의 migration 0001~0035를 현재 스키마 하나로 합쳤다. 이전 DB를 이 기준선으로 업그레이드하는
경로는 없다(보존할 운영 데이터가 없음). Alembic은 DB의 revision을 접두사로도 찾으므로, revision ID는 이전
체인의 어느 ID(0001~0035)로도 시작하지 않게 두어 이전 DB를 현재 head로 받아들이지 않는다.
새 스키마 변경은 이 기준선을 down_revision으로 두는 revision으로 추가한다.

Revision ID: sos_baseline
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "sos_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('at', sa.Text(), nullable=False),
    sa.Column('actor_id', sa.Text(), nullable=False),
    sa.Column('actor_type', sa.String(length=24), nullable=False),
    sa.Column('action', sa.String(length=96), nullable=False),
    sa.Column('resource_type', sa.String(length=40), nullable=False),
    sa.Column('resource_id', sa.Text(), nullable=True),
    sa.Column('decision', sa.String(length=12), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=True),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_events_at_id', 'audit_events', ['at', 'id'], unique=False)
    op.create_index('ix_audit_events_request_id', 'audit_events', ['request_id'], unique=False)
    op.create_index('ix_audit_events_resource_id', 'audit_events', ['resource_id'], unique=False)
    op.create_table('datasets',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('version', sa.Text(), nullable=False),
    sa.Column('parse_options', sa.Text(), nullable=True),
    sa.Column('row_count', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.Column('source_job_id', sa.String(length=36), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_datasets_created_at_id', 'datasets', ['created_at', 'id'], unique=False)
    op.create_index('ix_datasets_owner_created_at_id', 'datasets', ['owner_actor', 'created_at', 'id'], unique=False)
    op.create_index('ix_datasets_source_job', 'datasets', ['source_job_id'], unique=False)
    op.create_table('iceberg_table_owners',
    sa.Column('table_name', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=False),
    sa.Column('token', sa.String(length=36), nullable=False),
    sa.Column('table_uuid', sa.String(length=36), nullable=True),
    sa.Column('adoption_dataset_id', sa.String(length=36), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('table_name')
    )
    op.create_table('instance_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('dataset_id', sa.String(length=36), nullable=True),
    sa.Column('spec', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('failure_stage', sa.String(length=64), nullable=True),
    sa.Column('failure_category', sa.String(length=64), nullable=True),
    sa.Column('exception_type', sa.String(length=64), nullable=True),
    sa.Column('request_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # datasets와 jobs는 서로를 참조한다. 두 테이블을 만든 뒤 Dataset의 출처 Job 참조를 추가한다.
    op.create_foreign_key(None, 'datasets', 'jobs', ['source_job_id'], ['id'])
    op.create_index('ix_jobs_created_at_id', 'jobs', ['created_at', 'id'], unique=False)
    op.create_index('ix_jobs_dataset_created_id', 'jobs', ['dataset_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_jobs_playground_model_actor_created', 'jobs', [sa.literal_column("((spec::jsonb ->> 'model_revision_id'))"), 'owner_actor', sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False, postgresql_where=sa.text("kind = 'ml_playground'"))
    op.create_index('ix_jobs_queued_created', 'jobs', ['created_at'], unique=False, postgresql_where=sa.text("status = 'Queued'"))
    op.create_index('ix_jobs_request_id', 'jobs', ['request_id'], unique=False)
    op.create_table('model_groups',
    sa.Column('owner_actor', sa.Text(), nullable=False),
    sa.Column('registry_key', sa.String(length=40), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('archived_at', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('owner_actor', 'registry_key')
    )
    op.create_table('object_observations',
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.Text(), nullable=True),
    sa.Column('first_seen_at', sa.Text(), nullable=False),
    sa.Column('last_seen_at', sa.Text(), nullable=False),
    sa.Column('observation_count', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('location')
    )
    op.create_table('object_writes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('owner_kind', sa.String(length=16), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_object_writes_location', 'object_writes', ['location'], unique=False)
    op.create_index('ix_object_writes_owner', 'object_writes', ['owner_kind', 'owner_id'], unique=False)
    op.create_table('snapshot_retention',
    sa.Column('dataset_id', sa.String(length=36), nullable=False),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('snapshot_id', sa.Text(), nullable=False),
    sa.Column('tag_name', sa.Text(), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('dataset_id'),
    sa.UniqueConstraint('tag_name')
    )
    op.create_table('workers',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('heartbeat_at', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('attempts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.Text(), nullable=False),
    sa.Column('finished_at', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('failure_stage', sa.String(length=64), nullable=True),
    sa.Column('failure_category', sa.String(length=64), nullable=True),
    sa.Column('exception_type', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'number')
    )
    op.create_table('dataset_profiles',
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('dataset_id', sa.String(length=36), nullable=False),
    sa.Column('dataset_version', sa.Text(), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('result', sa.Text(), nullable=False),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('job_id')
    )
    op.create_index('ix_dataset_profiles_dataset_created', 'dataset_profiles', ['dataset_id', 'created_at', 'job_id'], unique=False)
    op.create_table('job_submissions',
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('submission_key', sa.String(length=128), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('actor_id', 'submission_key'),
    sa.UniqueConstraint('job_id')
    )
    op.create_table('mlflow_exports',
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('run_id', sa.String(length=128), nullable=True),
    sa.Column('destination_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error_code', sa.String(length=64), nullable=True),
    sa.Column('retry_after', sa.Text(), nullable=False),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('job_id')
    )
    op.create_index('ix_mlflow_exports_due', 'mlflow_exports', ['state', 'retry_after'], unique=False)
    op.create_table('model_revisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=False),
    sa.Column('dataset_id', sa.String(length=36), nullable=True),
    sa.Column('dataset_version', sa.Text(), nullable=True),
    sa.Column('algorithm', sa.String(length=64), nullable=False),
    sa.Column('source_kind', sa.String(length=32), nullable=False),
    sa.Column('source', sa.Text(), nullable=True),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('features', sa.Text(), nullable=False),
    sa.Column('params', sa.Text(), nullable=False),
    sa.Column('training_score_summary', sa.Text(), nullable=False),
    sa.Column('runtime', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('registry_key', sa.String(length=40), server_default='default', nullable=False),
    sa.Column('lifecycle_state', sa.String(length=16), server_default='Active', nullable=False),
    sa.Column('registry_model', sa.Text(), nullable=True),
    sa.Column('registry_version', sa.String(length=32), nullable=True),
    sa.Column('archived_at', sa.Text(), nullable=True),
    sa.CheckConstraint("(source_kind = 'sos_training' AND dataset_id IS NOT NULL AND dataset_version IS NOT NULL AND source IS NULL) OR (source_kind = 'mlflow_registry' AND dataset_id IS NULL AND dataset_version IS NULL AND source IS NOT NULL)", name='ck_model_revisions_source'),
    sa.CheckConstraint("lifecycle_state IN ('Active','Deleting','Deleted')", name='ck_model_lifecycle_state'),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id'),
    sa.UniqueConstraint('job_id')
    )
    op.create_index('ix_model_revisions_owner_created', 'model_revisions', ['owner_actor', 'created_at'], unique=False)
    op.create_table('model_offerings',
    sa.Column('model_revision_id', sa.String(length=36), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('offered_by', sa.Text(), nullable=False),
    sa.Column('offered_at', sa.Text(), nullable=False),
    sa.Column('withdrawn_by', sa.Text(), nullable=True),
    sa.Column('withdrawn_at', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['model_revision_id'], ['model_revisions.id'], ),
    sa.PrimaryKeyConstraint('model_revision_id')
    )
    op.create_index('ix_model_offerings_withdrawn_offered', 'model_offerings', ['withdrawn_at', 'offered_at'], unique=False)
    op.create_table('scoring_results',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=False),
    sa.Column('model_revision_id', sa.String(length=36), nullable=False),
    sa.Column('dataset_id', sa.String(length=36), nullable=False),
    sa.Column('dataset_version', sa.Text(), nullable=False),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('score_direction', sa.String(length=40), nullable=False),
    sa.Column('feature_names', sa.Text(), nullable=False),
    sa.Column('score_summary', sa.Text(), nullable=False),
    sa.Column('ranking_metrics', sa.Text(), nullable=True),
    sa.Column('label_column', sa.Text(), nullable=True),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['model_revision_id'], ['model_revisions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id'),
    sa.UniqueConstraint('job_id')
    )
    op.create_index('ix_scoring_results_model_created', 'scoring_results', ['model_revision_id', 'created_at'], unique=False)
    op.create_table('serving_endpoints',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=False),
    sa.Column('owner_actor_type', sa.Text(), nullable=False),
    sa.Column('desired_state', sa.Text(), nullable=False),
    sa.Column('active_model_revision_id', sa.String(length=36), nullable=True),
    sa.Column('pending_model_revision_id', sa.String(length=36), nullable=True),
    sa.Column('previous_model_revision_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.CheckConstraint("desired_state IN ('serving','stopped')", name='ck_serving_endpoints_state'),
    sa.CheckConstraint('active_model_revision_id IS NOT NULL OR pending_model_revision_id IS NOT NULL', name='ck_serving_endpoints_revision'),
    sa.CheckConstraint('pending_model_revision_id IS NULL OR pending_model_revision_id IS DISTINCT FROM active_model_revision_id', name='ck_serving_endpoints_pending'),
    sa.ForeignKeyConstraint(['active_model_revision_id'], ['model_revisions.id'], ),
    sa.ForeignKeyConstraint(['pending_model_revision_id'], ['model_revisions.id'], ),
    sa.ForeignKeyConstraint(['previous_model_revision_id'], ['model_revisions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_actor', 'name')
    )
    op.create_index('ix_serving_endpoints_active', 'serving_endpoints', ['active_model_revision_id'], unique=False)
    op.create_table('decision_policy_revisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=False),
    sa.Column('scoring_result_id', sa.String(length=36), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('alert_fraction', sa.Float(), nullable=True),
    sa.Column('decision_summary', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['scoring_result_id'], ['scoring_results.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id'),
    sa.UniqueConstraint('job_id')
    )
    op.create_index('ix_ml_policy_scoring', 'decision_policy_revisions', ['scoring_result_id'], unique=False)
    op.create_table('explanation_results',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=False),
    sa.Column('scoring_result_id', sa.String(length=36), nullable=True),
    sa.Column('playground_job_id', sa.String(length=36), nullable=True),
    sa.Column('model_revision_id', sa.String(length=36), nullable=False),
    sa.Column('row_index', sa.Integer(), nullable=True),
    sa.Column('method', sa.String(length=96), nullable=False),
    sa.Column('output', sa.Text(), nullable=False),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.CheckConstraint('(scoring_result_id IS NOT NULL AND row_index IS NOT NULL AND playground_job_id IS NULL) OR (scoring_result_id IS NULL AND row_index IS NULL AND playground_job_id IS NOT NULL)', name='ck_explanation_source_exactly_one'),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['model_revision_id'], ['model_revisions.id'], ),
    sa.ForeignKeyConstraint(['playground_job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['scoring_result_id'], ['scoring_results.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id'),
    sa.UniqueConstraint('job_id')
    )
    op.create_index('ix_explanations_playground', 'explanation_results', ['playground_job_id'], unique=False)
    op.create_index('ix_explanations_scoring_row', 'explanation_results', ['scoring_result_id', 'row_index'], unique=False)
    op.create_table('scoring_observation_parts',
    sa.Column('scoring_result_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('part', sa.Integer(), nullable=False),
    sa.Column('first_position', sa.BigInteger(), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('location', sa.Text(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.CheckConstraint("kind IN ('order','position')", name='ck_scoring_observation_parts_kind'),
    sa.ForeignKeyConstraint(['scoring_result_id'], ['scoring_results.id'], ),
    sa.PrimaryKeyConstraint('scoring_result_id', 'kind', 'part')
    )
    op.create_table('evaluation_revisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=False),
    sa.Column('scoring_result_id', sa.String(length=36), nullable=False),
    sa.Column('policy_revision_id', sa.String(length=36), nullable=True),
    sa.Column('label_coverage', sa.String(length=16), nullable=False),
    sa.Column('decision_metrics', sa.Text(), nullable=False),
    sa.Column('ranking_metrics', sa.Text(), nullable=True),
    sa.Column('owner_actor', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['attempts.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['policy_revision_id'], ['decision_policy_revisions.id'], ),
    sa.ForeignKeyConstraint(['scoring_result_id'], ['scoring_results.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id'),
    sa.UniqueConstraint('job_id')
    )
    op.create_index('ix_ml_evaluation_scoring', 'evaluation_revisions', ['scoring_result_id'], unique=False)


def downgrade() -> None:
    raise RuntimeError("스키마 기준선은 자동으로 되돌리지 않습니다.")
