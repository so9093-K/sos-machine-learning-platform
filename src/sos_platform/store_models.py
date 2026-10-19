"""불변 모델 판본을 조회하는 PostgreSQL 정본."""
import json

from sqlalchemy import text


class ModelStoreMixin:
    """모델 게시 자체는 Job 완료 트랜잭션에서 수행한다."""

    def get_model_revision(self, revision_id: str) -> dict | None:
        """실행 파일을 로딩하지 않고 모델 메타데이터만 조회한다."""
        with self.connection() as db:
            row = db.execute(
                text("SELECT * FROM model_revisions WHERE id=:id"), {"id": revision_id}
            ).mappings().first()
        if row is None:
            return None
        item = dict(row)
        for field in ("features", "params", "training_score_summary", "runtime"):
            item[field] = json.loads(item[field])
        return item
