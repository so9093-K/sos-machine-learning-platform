"""온라인 서빙 Endpoint의 배포 의도.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 실제 실행 상태는 Ray Serve가 정본이다. 여기에는 의도와 전환·롤백 대상만 남긴다.
    op.create_table(
        "serving_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("owner_actor", sa.Text, nullable=False),
        sa.Column("owner_actor_type", sa.Text, nullable=False),
        sa.Column("desired_state", sa.Text, nullable=False),
        sa.Column("active_model_revision_id", sa.String(36),
                  sa.ForeignKey("model_revisions.id")),
        sa.Column("pending_model_revision_id", sa.String(36),
                  sa.ForeignKey("model_revisions.id")),
        sa.Column("previous_model_revision_id", sa.String(36),
                  sa.ForeignKey("model_revisions.id")),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.UniqueConstraint("owner_actor", "name"),
        sa.CheckConstraint("desired_state IN ('serving','stopped')",
                           name="ck_serving_endpoints_state"),
        sa.CheckConstraint(
            "active_model_revision_id IS NOT NULL OR pending_model_revision_id IS NOT NULL",
            name="ck_serving_endpoints_revision",
        ),
        sa.CheckConstraint(
            "pending_model_revision_id IS NULL "
            "OR pending_model_revision_id IS DISTINCT FROM active_model_revision_id",
            name="ck_serving_endpoints_pending",
        ),
    )
    op.create_index("ix_serving_endpoints_active", "serving_endpoints",
                    ["active_model_revision_id"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM serving_endpoints LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade: serving endpoints exist")
    op.drop_index("ix_serving_endpoints_active", table_name="serving_endpoints")
    op.drop_table("serving_endpoints")
