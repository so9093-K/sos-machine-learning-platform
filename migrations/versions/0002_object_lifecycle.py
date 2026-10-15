"""객체 게시 의도·장기 대조 관측을 영속화한다.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "object_writes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_object_writes_location", "object_writes", ["location"])
    op.create_index("ix_object_writes_owner", "object_writes", ["owner_kind", "owner_id"])
    op.create_table(
        "object_observations",
        sa.Column("location", sa.Text(), primary_key=True),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("object_observations")
    op.drop_index("ix_object_writes_owner", table_name="object_writes")
    op.drop_index("ix_object_writes_location", table_name="object_writes")
    op.drop_table("object_writes")
