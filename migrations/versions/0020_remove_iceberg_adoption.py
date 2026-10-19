"""Iceberg 과도기 채택 증거 컬럼과 미완료 채택 상태를 정리한다.

Revision ID: 0020
Revises: 0019

Catalog·Dataset·Snapshot·Tag는 변경하지 않는다. 진행 중이던 채택은
쓰기 권한으로 승격하지 않고 차단 상태로 보존한다.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE iceberg_table_owners SET state='Blocked' WHERE state='Adopting'")
    op.drop_column("iceberg_table_owners", "adoption_dataset_id")


def downgrade() -> None:
    # 삭제된 과도기 채택 근거는 재구성하지 않는다. Blocked 기록도 쓰기를 허용하지 않는다.
    op.add_column("iceberg_table_owners", sa.Column("adoption_dataset_id", sa.String(36)))
