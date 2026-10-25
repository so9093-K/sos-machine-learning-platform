"""Dataset을 요구하지 않는 소량 Playground Job을 허용한다.

Revision ID: 0023
Revises: 0022
"""

from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("jobs", "dataset_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    # 데이터 참조가 없는 Job을 가짜 Dataset에 귀속시키지 않는다.
    op.execute("DELETE FROM job_submissions WHERE job_id IN "
               "(SELECT id FROM jobs WHERE dataset_id IS NULL)")
    op.execute("DELETE FROM attempts WHERE job_id IN "
               "(SELECT id FROM jobs WHERE dataset_id IS NULL)")
    op.execute("DELETE FROM jobs WHERE dataset_id IS NULL")
    op.alter_column("jobs", "dataset_id", existing_type=sa.String(length=36), nullable=False)
