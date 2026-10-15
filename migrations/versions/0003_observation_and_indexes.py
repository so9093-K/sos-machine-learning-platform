"""객체 세대별 관측과 Job 조회 인덱스를 추가한다.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 과거 관측값에 객체 동일성 증거가 없어 첫 새 관측부터 다시 집계한다.
    op.add_column("object_observations", sa.Column("fingerprint", sa.Text(), nullable=True))
    op.create_index("ix_jobs_created_at_id", "jobs", ["created_at", "id"])
    op.create_index("ix_attempts_job_number", "attempts", ["job_id", "number"])


def downgrade() -> None:
    op.drop_index("ix_attempts_job_number", table_name="attempts")
    op.drop_index("ix_jobs_created_at_id", table_name="jobs")
    op.drop_column("object_observations", "fingerprint")
