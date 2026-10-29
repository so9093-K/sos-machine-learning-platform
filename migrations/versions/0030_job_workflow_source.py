"""신뢰된 Workflow 실행 경로가 생성한 Job의 출처 연결.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("workflow_submission_id", sa.String(36)))
    op.add_column("jobs", sa.Column("workflow_stage", sa.String(128)))
    op.create_foreign_key(
        "fk_jobs_workflow_submission", "jobs", "workflow_submissions",
        ["workflow_submission_id"], ["id"],
    )
    op.create_check_constraint(
        "ck_jobs_workflow_source", "jobs",
        "(workflow_submission_id IS NULL AND workflow_stage IS NULL) OR "
        "(workflow_submission_id IS NOT NULL AND workflow_stage IS NOT NULL)",
    )
    # 기존 제출 키는 사용자가 지정할 수 있었으므로 출처로 소급 확정하지 않는다.


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM jobs WHERE workflow_submission_id IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Cannot downgrade: trusted Workflow Job sources exist")
    op.drop_constraint("ck_jobs_workflow_source", "jobs", type_="check")
    op.drop_constraint("fk_jobs_workflow_submission", "jobs", type_="foreignkey")
    op.drop_column("jobs", "workflow_stage")
    op.drop_column("jobs", "workflow_submission_id")
