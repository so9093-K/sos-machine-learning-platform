"""영속 실패 원인과 과거 감사 추적을 위한 인덱스.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("jobs", "attempts"):
        op.add_column(table, sa.Column("failure_category", sa.String(64)))
        op.add_column(table, sa.Column("exception_type", sa.String(64)))
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index("ix_audit_events_resource_id", "audit_events", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource_id", table_name="audit_events")
    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    for table in ("attempts", "jobs"):
        op.drop_column(table, "exception_type")
        op.drop_column(table, "failure_category")
