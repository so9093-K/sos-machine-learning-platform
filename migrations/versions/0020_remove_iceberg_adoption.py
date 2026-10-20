"""과도기 Iceberg 채택을 쓰기 차단 상태로 전환하되 기존 근거를 보존한다.

Revision ID: 0020
Revises: 0019

보존 중인 Dataset ID는 채택 기능이 제거돼도 버리지 않는다.
기존 Snapshot·Catalog·Dataset·Tag는 변경하지 않는다.
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE iceberg_table_owners SET state='Blocked' WHERE state='Adopting'")


def downgrade() -> None:
    # 채택 근거와 원본 상태가 남아 있으므로 이전 상태로 안전하게 복구한다.
    # 기존 서비스도 Adopting 상태의 쓰기를 거부한다.
    op.execute("UPDATE iceberg_table_owners SET state='Adopting' WHERE state='Blocked'")
