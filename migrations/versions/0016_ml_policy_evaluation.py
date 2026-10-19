"""Decision Policy·Evaluation의 불변 결과를 Scoring과 분리한다.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_policy_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False, unique=True),
        sa.Column(
            "scoring_result_id", sa.String(36), sa.ForeignKey("scoring_results.id"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("alert_fraction", sa.Float()),
        sa.Column("decision_summary", sa.Text(), nullable=False),
        sa.Column("owner_actor", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "evaluation_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False, unique=True),
        sa.Column(
            "scoring_result_id", sa.String(36), sa.ForeignKey("scoring_results.id"),
            nullable=False,
        ),
        sa.Column(
            "policy_revision_id", sa.String(36),
            sa.ForeignKey("decision_policy_revisions.id"),
        ),
        sa.Column("label_coverage", sa.String(16), nullable=False),
        sa.Column("decision_metrics", sa.Text(), nullable=False),
        sa.Column("ranking_metrics", sa.Text()),
        sa.Column("owner_actor", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_ml_policy_scoring", "decision_policy_revisions", ["scoring_result_id"]
    )
    op.create_index(
        "ix_ml_evaluation_scoring", "evaluation_revisions", ["scoring_result_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ml_evaluation_scoring", table_name="evaluation_revisions")
    op.drop_index("ix_ml_policy_scoring", table_name="decision_policy_revisions")
    op.drop_table("evaluation_revisions")
    op.drop_table("decision_policy_revisions")
