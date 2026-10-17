"""Iceberg 테이블 쓰기 소유권을 Snapshot Dataset 참조와 분리한다.

Revision ID: 0011
Revises: 0010

기존 Catalog 테이블은 소유권을 추측해 자동 인수하지 않는다.
읽기와 보존은 그대로 유지하고 새 쓰기만 명시적 소유 토큰을 요구한다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iceberg_table_owners",
        sa.Column("table_name", sa.Text(), primary_key=True),
        sa.Column("owner_actor", sa.Text(), nullable=False),
        sa.Column("token", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("iceberg_table_owners")
