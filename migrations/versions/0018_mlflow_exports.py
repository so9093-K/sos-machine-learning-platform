"""선택적 MLflow 전송 상태를 ML 실행 정본과 분리한다.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mlflow_exports",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(128)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("retry_after", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_mlflow_exports_due", "mlflow_exports", ["state", "retry_after"])


def downgrade() -> None:
    op.drop_index("ix_mlflow_exports_due", table_name="mlflow_exports")
    op.drop_table("mlflow_exports")
