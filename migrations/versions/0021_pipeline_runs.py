"""등록형 Pipeline Run과 요청 멱등성.

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("definition_id", sa.String(96), nullable=False),
        sa.Column("definition_version", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("owner_actor", sa.Text(), nullable=False),
        sa.Column("owner_actor_type", sa.String(16), nullable=False),
        sa.Column("input_plan", sa.Text(), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("job_ids", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_pipeline_run_owner_created", "pipeline_runs", ["owner_actor", "created_at"])
    op.create_table(
        "pipeline_submissions",
        sa.Column("actor_id", sa.Text(), primary_key=True),
        sa.Column("submission_key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("pipeline_runs.id", deferrable=True, initially="DEFERRED"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pipeline_submissions")
    op.drop_index("ix_pipeline_run_owner_created", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
