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
    # 실행·입력 기록을 자동 삭제하거나 가짜 Dataset을 할당하지 않는다.
    pending = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM jobs WHERE dataset_id IS NULL")
    ).scalar_one()
    if pending:
        raise RuntimeError(
            "Dataset 없는 Playground Job 이력이 남아 있습니다. "
            "이력을 명시적으로 정리하기 전에는 스키마를 되돌릴 수 없습니다."
        )
    op.alter_column("jobs", "dataset_id", existing_type=sa.String(length=36), nullable=False)
