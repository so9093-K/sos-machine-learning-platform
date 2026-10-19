"""ML 학습 결과의 불변 Model Revision 정본.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False, unique=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.String(64), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("features", sa.Text(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("training_score_summary", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("owner_actor", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_model_revisions_owner_created", "model_revisions", ["owner_actor", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_revisions_owner_created", table_name="model_revisions")
    op.drop_table("model_revisions")
