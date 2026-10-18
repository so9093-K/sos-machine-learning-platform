"""Dataset 원본의 헤더/구분자 해석 설정을 함께 보존한다.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("parse_options", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "parse_options")
