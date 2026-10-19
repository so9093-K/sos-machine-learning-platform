"""개별 점수 설명을 불변 Scoring Result에 연결한다.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "explanation_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False, unique=True),
        sa.Column(
            "scoring_result_id", sa.String(36), sa.ForeignKey("scoring_results.id"), nullable=False
        ),
        sa.Column(
            "model_revision_id", sa.String(36), sa.ForeignKey("model_revisions.id"), nullable=False
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(96), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("owner_actor", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_explanations_scoring_row",
        "explanation_results",
        ["scoring_result_id", "row_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_explanations_scoring_row", table_name="explanation_results")
    op.drop_table("explanation_results")
