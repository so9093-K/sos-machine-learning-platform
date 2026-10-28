"""Dataset을 생성한 Job ID를 파일 파싱 설정과 분리해 불변 계보로 기록한다.

Revision ID: 0028
Revises: 0027
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 기존 Dataset은 과거 생성 Job을 추정하지 않고 NULL로 유지한다.
    op.add_column("datasets", sa.Column(
        "source_job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True
    ))
    op.create_index("ix_datasets_source_job", "datasets", ["source_job_id"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM datasets WHERE source_job_id IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Cannot downgrade: immutable Dataset source Job references exist")
    op.drop_index("ix_datasets_source_job", table_name="datasets")
    op.drop_column("datasets", "source_job_id")
