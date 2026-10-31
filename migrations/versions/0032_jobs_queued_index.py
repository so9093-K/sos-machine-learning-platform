"""Worker가 대기 Job을 찾을 때 완료된 실행 기록 전체를 읽지 않게 한다.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Worker는 짧은 간격으로 가장 오래된 Queued Job을 찾는다. 완료 기록이 쌓여도 대기 Job만 본다.
    op.execute("CREATE INDEX ix_jobs_queued_created ON jobs (created_at) WHERE status = 'Queued'")


def downgrade() -> None:
    op.drop_index("ix_jobs_queued_created", table_name="jobs")
