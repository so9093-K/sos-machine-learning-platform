"""완료된 Dataset Understanding 결과를 원본과 별도 불변 자산으로 기록한다.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_profiles",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_dataset_profiles_dataset_created",
        "dataset_profiles",
        ["dataset_id", "created_at", "job_id"],
    )


    op.create_index(
        "ix_jobs_dataset_created_id",
        "jobs",
        ["dataset_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_dataset_created_id", table_name="jobs")
    op.drop_index("ix_dataset_profiles_dataset_created", table_name="dataset_profiles")
    op.drop_table("dataset_profiles")
