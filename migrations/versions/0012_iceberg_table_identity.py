"""Iceberg 테이블 물리 UUID 및 관리자 채택 근거를 고정한다.

Revision ID: 0012
Revises: 0011

이미 0011까지 적용된 테스트·사전 배포 DB에도 전진 마이그레이션한다.
UUID가 없는 기존 Active 레코드는 자동으로 재소유하지 않고 쓰기를 거부한다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("iceberg_table_owners", sa.Column("table_uuid", sa.String(36)))
    op.add_column("iceberg_table_owners", sa.Column("adoption_dataset_id", sa.String(36)))


def downgrade() -> None:
    op.drop_column("iceberg_table_owners", "adoption_dataset_id")
    op.drop_column("iceberg_table_owners", "table_uuid")
