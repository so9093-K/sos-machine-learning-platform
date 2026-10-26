"""모델 그룹 키와 안전한 물리 삭제 상태.

Revision ID: 0025
Revises: 0024
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_revisions",
        sa.Column("registry_key", sa.String(40), nullable=False, server_default="default"),
    )
    op.add_column(
        "model_revisions",
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="Active"),
    )
    op.create_check_constraint(
        "ck_model_lifecycle_state", "model_revisions",
        "lifecycle_state IN ('Active','Deleting','Deleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_model_lifecycle_state", "model_revisions", type_="check")
    op.drop_column("model_revisions", "lifecycle_state")
    op.drop_column("model_revisions", "registry_key")
