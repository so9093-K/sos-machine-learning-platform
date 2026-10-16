"""Job 제출 신원의 트랜잭션 일관성.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_submissions",
        sa.Column("actor_id", sa.String(128), primary_key=True),
        sa.Column("submission_key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_submissions")
