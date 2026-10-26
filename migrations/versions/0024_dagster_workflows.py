"""Dagster Workflow 제출 연결로 자체 Pipeline 상태 머신 교체.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 사용자의 과거 분석 이력을 예고 없이 삭제하지 않는다. 참조된 SOS
    # Job·Model·Scoring 결과는 보존하되 기존 Workflow 이력의 처리 정책을
    # 결정하기 전에 기존 테이블을 파괴해서는 안 된다.
    pending = bind.execute(sa.text("SELECT count(*) FROM pipeline_runs")).scalar_one()
    if pending:
        raise RuntimeError(
            "기존 Pipeline Run 이력이 남아 있습니다. 실행 결과를 확인·보관한 뒤 "
            "전환을 진행해야 합니다. 자동으로 이력을 삭제하지 않습니다."
        )
    op.create_table(
        "workflow_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("submission_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("definition_id", sa.String(96), nullable=False),
        sa.Column("definition_version", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("owner_actor", sa.Text(), nullable=False),
        sa.Column("owner_actor_type", sa.String(16), nullable=False),
        sa.Column("dagster_run_id", sa.String(36), nullable=True, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("actor_id", "submission_key", name="uq_workflow_submission_key"),
    )
    op.create_index("ix_workflow_owner_created", "workflow_submissions", ["owner_actor", "created_at"])
    op.drop_table("pipeline_submissions")
    op.drop_index("ix_pipeline_run_owner_created", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")


def downgrade() -> None:
    raise RuntimeError("Dagster 전환은 이전 자체 Pipeline 엔진으로 자동 복구하지 않습니다.")
