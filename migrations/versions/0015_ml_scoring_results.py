"""불변 Scoring Result를 모델과 평가 입력으로 재사용한다.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scoring_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column(
            "attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False, unique=True
        ),
        sa.Column(
            "model_revision_id", sa.String(36), sa.ForeignKey("model_revisions.id"), nullable=False
        ),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("score_direction", sa.String(40), nullable=False),
        sa.Column("feature_names", sa.Text(), nullable=False),
        sa.Column("score_summary", sa.Text(), nullable=False),
        sa.Column("ranking_metrics", sa.Text()),
        sa.Column("label_column", sa.Text()),
        sa.Column("owner_actor", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_scoring_results_model_created", "scoring_results", ["model_revision_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_scoring_results_model_created", table_name="scoring_results")
    op.drop_table("scoring_results")
