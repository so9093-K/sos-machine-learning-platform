"""MLflow Registry의 고정 모델 이름·버전 참조를 실행 근거와 연결한다."""

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_revisions", sa.Column("registry_model", sa.Text(), nullable=True))
    op.add_column("model_revisions", sa.Column("registry_version", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("model_revisions", "registry_version")
    op.drop_column("model_revisions", "registry_model")
