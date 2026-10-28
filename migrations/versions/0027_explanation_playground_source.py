"""Explanation 결과의 선택적 출처를 단일 Playground Job까지 확장한다.

Revision ID: 0027
Revises: 0026
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("explanation_results", "scoring_result_id", existing_type=sa.String(36),
                    nullable=True)
    op.alter_column("explanation_results", "row_index", existing_type=sa.Integer(),
                    nullable=True)
    op.add_column("explanation_results", sa.Column(
        "playground_job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True))
    op.create_index("ix_explanations_playground", "explanation_results", ["playground_job_id"])
    op.create_check_constraint(
        "ck_explanation_source_exactly_one", "explanation_results",
        "(scoring_result_id IS NOT NULL AND row_index IS NOT NULL AND playground_job_id IS NULL) "
        "OR (scoring_result_id IS NULL AND row_index IS NULL AND playground_job_id IS NOT NULL)",
    )


def downgrade() -> None:
    # 새 출처를 참조하는 불변 설명 기록은 기존 NOT NULL 구조로 복원할 수 없다.
    # 자동 삭제로 기록·감사를 손실하지 않고 명시적으로 중단한다.
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM explanation_results WHERE playground_job_id IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError(
            "Cannot downgrade: immutable Playground Explanation records exist"
        )
    op.drop_constraint("ck_explanation_source_exactly_one", "explanation_results", type_="check")
    op.drop_index("ix_explanations_playground", table_name="explanation_results")
    op.drop_column("explanation_results", "playground_job_id")
    op.alter_column("explanation_results", "row_index", existing_type=sa.Integer(),
                    nullable=False)
    op.alter_column("explanation_results", "scoring_result_id",
                    existing_type=sa.String(36), nullable=False)
