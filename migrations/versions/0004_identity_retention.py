"""Snapshot 보존 의도와 Actor 권한 감사.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("owner_actor", sa.Text()))
    op.add_column("jobs", sa.Column("owner_actor", sa.Text()))
    op.create_table(
        "snapshot_retention",
        sa.Column("dataset_id", sa.String(36), primary_key=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("tag_name", sa.Text(), nullable=False, unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("at", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("action", sa.String(96), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Text()),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("request_id", sa.String(36)),
        sa.Column("reason", sa.String(200)),
    )
    op.create_index("ix_audit_events_at_id", "audit_events", ["at", "id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_at_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("snapshot_retention")
    op.drop_column("jobs", "owner_actor")
    op.drop_column("datasets", "owner_actor")
