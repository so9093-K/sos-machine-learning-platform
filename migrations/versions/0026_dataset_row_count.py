"""등록 시 전수 검사한 텍스트 Dataset의 데이터 행 수.

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 기존 Dataset은 다시 읽어 추정하지 않고 NULL(미계산)로 둔다.
    op.add_column("datasets", sa.Column("row_count", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "row_count")
