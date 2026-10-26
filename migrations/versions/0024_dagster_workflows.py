"""Dagster Workflow 제출 연결로 자체 Pipeline 상태 머신 교체.

Revision ID: 0024
Revises: 0023
"""
import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _retire_old_workflow_history(bind) -> None:
    """이전 실행 그룹을 감사 흔적으로 남기고 도메인 Job과 결과는 보존한다.

    이전 자체 Orchestrator의 내부 상태와 실행 테이블 자체는 옮기지 않는다.
    실제 Job/Attempt·Model·Scoring·Evaluation 불변 판본은 별도 테이블에 남는다.
    """
    rows = bind.execute(sa.text(
        "SELECT id,owner_actor,owner_actor_type,job_ids FROM pipeline_runs"
    )).mappings().all()
    insert = sa.text(
        "INSERT INTO audit_events "
        "(id,at,actor_id,actor_type,action,resource_type,resource_id,"
        "decision,outcome,reason) "
        "VALUES (:id,:at,:actor,:type,:action,:resource_type,:resource_id,"
        "'allow','succeeded',:reason)"
    )
    for row in rows:
        props = {
            "actor": row["owner_actor"],
            "type": row["owner_actor_type"],
            "at": datetime.now(UTC).isoformat(),
        }
        bind.execute(insert, {
            **props, "id": str(uuid4()),
            "action": "workflow.retired",
            "resource_type": "workflow",
            "resource_id": row["id"],
            "reason": "Dagster OSS 전환으로 기존 Workflow 그룹 정보를 종료했습니다.",
        })
        refs = json.loads(row["job_ids"])
        if not isinstance(refs, dict):
            raise RuntimeError("기존 Pipeline Job 참조 형식이 올바르지 않습니다.")
        for stage, job_id in refs.items():
            bind.execute(insert, {
                **props, "id": str(uuid4()),
                "action": "workflow.job_link_retired",
                "resource_type": "job",
                "resource_id": job_id,
                "reason": f"이전 Workflow {row['id']}의 {stage} 단계; Job은 보존합니다."[:200],
            })


def upgrade() -> None:
    bind = op.get_bind()
    _retire_old_workflow_history(bind)
    op.create_table(
        "workflow_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("submission_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("definition_id", sa.String(96), nullable=False),
        sa.Column("definition_version", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("owner_actor", sa.Text(), nullable=False),
        sa.Column("owner_actor_type", sa.String(16), nullable=False),
        sa.Column("dagster_run_id", sa.String(36), nullable=True, unique=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("actor_id", "submission_key", name="uq_workflow_submission_key"),
    )
    op.create_index("ix_workflow_owner_created", "workflow_submissions", ["owner_actor", "created_at"])
    op.drop_table("pipeline_submissions")
    op.drop_index("ix_pipeline_run_owner_created", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")


def downgrade() -> None:
    raise RuntimeError("Dagster 전환은 이전 자체 Pipeline 엔진으로 자동 복구하지 않습니다.")
