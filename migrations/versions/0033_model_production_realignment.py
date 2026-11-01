"""모델 생산 책임 재정렬: Model Revision 출처 기록과 SOS Pipeline 제출 경로 제거.

모델 생산(학습·평가·MLflow 등록)은 Dagster가 직접 실행하고, SOS는 MLflow 모델 버전을 연결한다.
연결한 판본은 SOS Dataset 대신 MLflow 출처를 가지므로 Dataset 참조를 선택 항목으로 바꾼다.

Revision ID: 0033
Revises: 0032
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def _retire_workflow_submissions(bind) -> None:
    """제출 기록은 감사 흔적으로 남기고, 단계 Job·Model·Scoring 판본은 그대로 보존한다."""
    at = datetime.now(UTC).isoformat()
    rows = bind.execute(sa.text(
        "SELECT id,owner_actor,owner_actor_type,dagster_run_id FROM workflow_submissions"
    )).mappings().all()
    for row in rows:
        bind.execute(sa.text(
            "INSERT INTO audit_events "
            "(id,at,actor_id,actor_type,action,resource_type,resource_id,decision,outcome,reason) "
            "VALUES (:id,:at,:actor,:type,'workflow.retired','workflow',:resource,"
            "'allow','succeeded',:reason)"
        ), {
            "id": str(uuid4()), "at": at, "actor": row["owner_actor"],
            "type": row["owner_actor_type"], "resource": row["id"],
            "reason": f"SOS Pipeline 제출 경로 제거; Dagster Run {row['dagster_run_id'] or '없음'}"[:200],
        })
    # 실행할 연산이 사라진 대기 사전 검사는 취소로 끝낸다. 실행 중이던 Attempt는 Worker 복구가 종료한다.
    bind.execute(sa.text(
        "UPDATE jobs SET status='Cancelled',updated_at=:at "
        "WHERE kind='ml_pipeline_preflight' AND status='Queued'"
    ), {"at": at})


def upgrade() -> None:
    _retire_workflow_submissions(op.get_bind())
    op.drop_constraint("ck_jobs_workflow_source", "jobs", type_="check")
    op.drop_constraint("fk_jobs_workflow_submission", "jobs", type_="foreignkey")
    op.drop_column("jobs", "workflow_stage")
    op.drop_column("jobs", "workflow_submission_id")
    op.drop_index("ix_workflow_owner_created", table_name="workflow_submissions")
    op.drop_table("workflow_submissions")

    op.add_column("model_revisions", sa.Column(
        "source_kind", sa.String(32), nullable=False, server_default="sos_training",
    ))
    # 기존 판본은 모두 SOS 학습 판본이다. 이후 게시는 출처를 항상 명시한다.
    op.alter_column("model_revisions", "source_kind", server_default=None)
    op.add_column("model_revisions", sa.Column("source", sa.Text(), nullable=True))
    op.alter_column("model_revisions", "dataset_id", existing_type=sa.String(36), nullable=True)
    op.alter_column("model_revisions", "dataset_version", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        "ck_model_revisions_source", "model_revisions",
        "(source_kind = 'sos_training' AND dataset_id IS NOT NULL AND dataset_version IS NOT NULL "
        "AND source IS NULL) OR "
        "(source_kind = 'mlflow_registry' AND dataset_id IS NULL AND dataset_version IS NULL "
        "AND source IS NOT NULL)",
    )


def downgrade() -> None:
    raise RuntimeError("SOS Pipeline 제출 경로와 Workflow 제출 기록은 자동 복구하지 않습니다.")
