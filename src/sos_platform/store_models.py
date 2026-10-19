"""모델과 독립 Scoring Result의 영속 조회 계약."""

import json

from sqlalchemy import text


class ModelStoreMixin:
    """모델 및 점수 결과 게시 자체는 Job 종료 트랜잭션이 수행한다."""

    def get_model_revision(self, revision_id: str) -> dict | None:
        """실행 파일을 로딩하지 않고 모델 메타데이터만 조회한다."""
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM model_revisions WHERE id=:id"), {"id": revision_id})
                .mappings()
                .first()
            )
        if row is None:
            return None
        item = dict(row)
        for field in ("features", "params", "training_score_summary", "runtime"):
            item[field] = json.loads(item[field])
        return item

    def get_scoring_result(self, result_id: str) -> dict | None:
        """Scoring Artifact 바이트와 독립된 판본·평가 근거를 반환한다."""
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM scoring_results WHERE id=:id"), {"id": result_id})
                .mappings()
                .first()
            )
        if row is None:
            return None
        item = dict(row)
        for field in ("feature_names", "score_summary", "ranking_metrics"):
            if item[field] is not None:
                item[field] = json.loads(item[field])
        return item

    def get_decision_policy_revision(self, revision_id: str) -> dict | None:
        """정책 경계를 모델·원본 점수 결과와 독립적으로 조회한다."""
        with self.connection() as db:
            row = db.execute(
                text("SELECT * FROM decision_policy_revisions WHERE id=:id"),
                {"id": revision_id},
            ).mappings().first()
        if row is None:
            return None
        item = dict(row)
        item["decision_summary"] = json.loads(item["decision_summary"])
        return item

    def get_evaluation_revision(self, revision_id: str) -> dict | None:
        """평가 조건·레이블 커버리지·지표의 불변 근거."""
        with self.connection() as db:
            row = db.execute(
                text("SELECT * FROM evaluation_revisions WHERE id=:id"),
                {"id": revision_id},
            ).mappings().first()
        if row is None:
            return None
        item = dict(row)
        for field in ("decision_metrics", "ranking_metrics"):
            if item[field] is not None:
                item[field] = json.loads(item[field])
        return item
