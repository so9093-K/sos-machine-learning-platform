"""Dataset 키셋 페이지네이션을 위한 인덱스.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_datasets_created_at_id", "datasets", ["created_at", "id"])
    op.create_index(
        "ix_datasets_owner_created_at_id",
        "datasets",
        ["owner_actor", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_datasets_owner_created_at_id", table_name="datasets")
    op.drop_index("ix_datasets_created_at_id", table_name="datasets")
