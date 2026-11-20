"""모델 제공에 공급자가 명시적으로 공개한 평가 근거.

Revision ID: sos_offering_evaluations
Revises: sos_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "sos_offering_evaluations"
down_revision = "sos_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_offering_evaluations",
        sa.Column("model_revision_id", sa.String(length=36), nullable=False),
        sa.Column("evaluation_revision_id", sa.String(length=36), nullable=False),
        # 공급자가 쓴 기준 데이터·조건 설명. 평가 Dataset 자체는 공개하지 않는다.
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.Text(), nullable=False),
        sa.Column("published_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["model_revision_id"], ["model_revisions.id"]),
        sa.ForeignKeyConstraint(["evaluation_revision_id"], ["evaluation_revisions.id"]),
        sa.PrimaryKeyConstraint("model_revision_id", "evaluation_revision_id"),
    )


def downgrade() -> None:
    op.drop_table("model_offering_evaluations")
