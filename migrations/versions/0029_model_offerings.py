"""모델 소유자가 다른 Actor에게 명시적으로 제공한 모델 판본.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 판본당 한 행을 두고, 제공 중단은 삭제 대신 중단 시각으로 남긴다.
    op.create_table(
        "model_offerings",
        sa.Column(
            "model_revision_id",
            sa.String(36),
            sa.ForeignKey("model_revisions.id"),
            primary_key=True,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("offered_by", sa.Text, nullable=False),
        sa.Column("offered_at", sa.Text, nullable=False),
        sa.Column("withdrawn_by", sa.Text),
        sa.Column("withdrawn_at", sa.Text),
    )
    op.create_index(
        "ix_model_offerings_withdrawn_offered",
        "model_offerings",
        ["withdrawn_at", "offered_at"],
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM model_offerings LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade: model offering records exist")
    op.drop_index("ix_model_offerings_withdrawn_offered", table_name="model_offerings")
    op.drop_table("model_offerings")
