"""Job/Attempt 진단 상관관계 및 오류 코드 기록.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("request_id", sa.String(36)))
    op.add_column("jobs", sa.Column("error_code", sa.String(64)))
    op.add_column("jobs", sa.Column("failure_stage", sa.String(64)))
    op.add_column("attempts", sa.Column("error_code", sa.String(64)))
    op.add_column("attempts", sa.Column("failure_stage", sa.String(64)))
    op.create_index("ix_jobs_request_id", "jobs", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_request_id", table_name="jobs")
    op.drop_column("attempts", "failure_stage")
    op.drop_column("attempts", "error_code")
    op.drop_column("jobs", "failure_stage")
    op.drop_column("jobs", "error_code")
    op.drop_column("jobs", "request_id")
