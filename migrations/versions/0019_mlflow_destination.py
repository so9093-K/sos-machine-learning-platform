"""외부 Tracking Run ID의 최초 서버·Experiment 정체성 고정.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mlflow_exports", sa.Column("destination_fingerprint", sa.String(64)))


def downgrade() -> None:
    op.drop_column("mlflow_exports", "destination_fingerprint")
