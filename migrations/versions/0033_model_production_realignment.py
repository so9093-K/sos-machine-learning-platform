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


def _audit(bind, at: str, *, actor: str, actor_type: str, action: str, resource_type: str,
           resource: str, reason: str) -> None:
    bind.execute(sa.text(
        "INSERT INTO audit_events "
        "(id,at,actor_id,actor_type,action,resource_type,resource_id,decision,outcome,reason) "
        "VALUES (:id,:at,:actor,:type,:action,:resource_type,:resource,'allow','succeeded',:reason)"
    ), {
        "id": str(uuid4()), "at": at, "actor": actor, "type": actor_type, "action": action,
        "resource_type": resource_type, "resource": resource, "reason": reason[:200],
    })


def _retire_workflow_jobs(bind, at: str) -> None:
    """폐기하는 Workflow가 만든 미완료 단계 Job을 Workflow 연결 컬럼을 지우기 전에 정리한다.

    대상은 Workflow 제출에 연결된 Job과, Workflow로만 만들어지던 사전 검사 Job이다. 독립적으로 제출한
    Job과 이미 끝난 Job·결과 판본은 바꾸지 않는다.

    - Queued: 시도가 없으므로 바로 취소로 끝낸다. 실행된 적 없는 단계라 MLflow Run을 만들지 않고,
      폐기 사유는 감사 이벤트로 남긴다.
    - Running: 취소를 요청한다. 실행 중인 Worker는 기존 취소 확인으로 중단·확정하고, Worker가 이미
      멈췄다면 다음 Worker의 복구가 취소로 확정한다(사전 검사도 다시 대기열에 넣지 않는다). 두 경로
      모두 Worker의 기존 종료 경로이므로 ML Job의 MLflow 기록도 그대로 남는다.
    """
    rows = bind.execute(sa.text(
        "SELECT id,status,workflow_submission_id FROM jobs "
        "WHERE status IN ('Queued','Running') "
        "AND (workflow_submission_id IS NOT NULL OR kind='ml_pipeline_preflight') FOR UPDATE"
    )).mappings().all()
    for row in rows:
        if row["status"] == "Queued":
            bind.execute(sa.text(
                "UPDATE jobs SET status='Cancelled',cancel_requested=true,updated_at=:at WHERE id=:id"
            ), {"at": at, "id": row["id"]})
        else:
            bind.execute(sa.text(
                "UPDATE jobs SET cancel_requested=true,updated_at=:at WHERE id=:id"
            ), {"at": at, "id": row["id"]})
        _audit(
            bind, at, actor="sos-migration", actor_type="service", action="job.cancel",
            resource_type="job", resource=row["id"],
            reason=f"SOS Pipeline 제출 경로 제거; Workflow {row['workflow_submission_id'] or '연결 없음'}",
        )


def _retire_workflow_submissions(bind, at: str) -> None:
    """제출 기록은 감사 흔적으로 남기고, 단계 Job·Model·Scoring 판본은 그대로 보존한다."""
    rows = bind.execute(sa.text(
        "SELECT id,owner_actor,owner_actor_type,dagster_run_id FROM workflow_submissions"
    )).mappings().all()
    for row in rows:
        _audit(
            bind, at, actor=row["owner_actor"], actor_type=row["owner_actor_type"],
            action="workflow.retired", resource_type="workflow", resource=row["id"],
            reason=f"SOS Pipeline 제출 경로 제거; Dagster Run {row['dagster_run_id'] or '없음'}",
        )


def upgrade() -> None:
    bind = op.get_bind()
    at = datetime.now(UTC).isoformat()
    _retire_workflow_jobs(bind, at)
    _retire_workflow_submissions(bind, at)
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
