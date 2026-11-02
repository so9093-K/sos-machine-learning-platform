"""SOS가 소유하는 모델 사용 판정: 판본 보관과 모델 그룹.

모델 판본의 신규 사용 허용은 SOS 상태(수명주기·판본 보관·그룹 보관)로만 판정한다. 모델 그룹의
이름·설명·보관도 SOS가 소유한다. 기존 판본의 (소유 Actor, 그룹 키)마다 그룹 행을 만든다.
MLflow의 이전 보관 태그는 옮기지 않는다(ADR 0025).

Revision ID: 0035
Revises: 0034
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_revisions", sa.Column("archived_at", sa.Text(), nullable=True))
    op.create_table(
        "model_groups",
        sa.Column("owner_actor", sa.Text(), primary_key=True),
        sa.Column("registry_key", sa.String(40), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    at = datetime.now(UTC).isoformat()
    op.get_bind().execute(sa.text(
        "INSERT INTO model_groups(owner_actor,registry_key,title,description,archived_at,"
        "created_at,updated_at) "
        "SELECT DISTINCT owner_actor, registry_key, "
        "CASE WHEN registry_key='default' THEN :default_title ELSE registry_key END, "
        "'', NULL, :at, :at FROM model_revisions WHERE owner_actor IS NOT NULL"
    ), {"default_title": "기본 활동 이상 탐지", "at": at})


def downgrade() -> None:
    raise RuntimeError("모델 사용 판정의 정본 전환은 자동으로 되돌리지 않습니다.")
