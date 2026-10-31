"""Worker가 대기 Job을 찾을 때 완료된 실행 기록 전체를 읽지 않게 한다.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Worker는 짧은 간격으로 가장 오래된 Queued Job을 찾는다. 완료 기록이 쌓여도 대기 Job만 본다.
    # 운영 중인 큰 jobs 테이블에서도 Job 생성·갱신을 막지 않도록 CONCURRENTLY로 만든다. 이 명령은
    # 트랜잭션 안에서 실행할 수 없으므로 autocommit으로 실행한다.
    with op.get_context().autocommit_block():
        # 이전 시도가 중단되면 무효(INVALID) 인덱스가 남는다. 남은 것은 지우고 다시 만든다.
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_queued_created")
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_jobs_queued_created ON jobs (created_at) "
            "WHERE status = 'Queued'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_queued_created")
