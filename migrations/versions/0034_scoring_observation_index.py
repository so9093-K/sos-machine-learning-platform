"""Scoring 결과의 점수순 관측값 색인 조각과 기존 결과의 색인 Job.

관측값 탐색은 결과마다 한 번 만든 불변 색인 조각을 재사용한다. 이미 있는 결과는 같은 Worker 실행 계약
(Job/Attempt·게시 의도·Stage 감시)으로 색인을 만들도록 Job을 대기열에 넣는다.

Revision ID: 0034
Revises: 0033
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scoring_observation_parts",
        sa.Column("scoring_result_id", sa.String(36), sa.ForeignKey("scoring_results.id"),
                  primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("part", sa.Integer(), primary_key=True),
        sa.Column("first_position", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("kind IN ('order','position')", name="ck_scoring_observation_parts_kind"),
    )
    bind = op.get_bind()
    at = datetime.now(UTC).isoformat()
    results = bind.execute(sa.text("SELECT id,owner_actor FROM scoring_results")).mappings().all()
    for result in results:
        job_id = str(uuid4())
        bind.execute(sa.text(
            "INSERT INTO jobs(id,kind,dataset_id,spec,status,cancel_requested,created_at,updated_at,"
            "owner_actor) VALUES(:id,'ml_score_index',NULL,:spec,'Queued',false,:at,:at,:owner)"
        ), {
            "id": job_id, "at": at, "owner": result["owner_actor"],
            "spec": json.dumps({
                "scoring_result_id": result["id"], "source_owner_actor": result["owner_actor"],
            }),
        })
        bind.execute(sa.text(
            "INSERT INTO audit_events "
            "(id,at,actor_id,actor_type,action,resource_type,resource_id,decision,outcome,reason) "
            "VALUES (:id,:at,'sos-migration','service','job.create','job',:job,'allow','succeeded',"
            ":reason)"
        ), {"id": str(uuid4()), "at": at, "job": job_id,
            "reason": f"기존 Scoring 결과 {result['id']}의 관측값 색인"})


def downgrade() -> None:
    raise RuntimeError("관측값 색인 조각과 색인 Job은 자동으로 되돌리지 않습니다.")
