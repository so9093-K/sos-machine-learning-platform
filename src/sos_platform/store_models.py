"""모델과 독립 Scoring Result의 영속 조회 계약."""

import json

from sqlalchemy import text

from .store_administration import now

MODEL_JSON_FIELDS = ("features", "params", "training_score_summary", "runtime")


class ModelOfferingUnavailable(Exception):
    """제공할 수 없는 모델 상태. 사유 문장은 사용자 안내에 그대로 쓴다."""


def _model_item(row) -> dict:
    item = dict(row)
    for field in MODEL_JSON_FIELDS:
        item[field] = json.loads(item[field])
    return item


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
        return None if row is None else _model_item(row)

    def get_model_offering(self, revision_id: str) -> dict | None:
        """제공 중이거나 제공이 중단된 판본의 기록. 제공한 적이 없으면 None이다."""
        with self.connection() as db:
            row = db.execute(
                text("SELECT * FROM model_offerings WHERE model_revision_id=:id"),
                {"id": revision_id},
            ).mappings().first()
        return None if row is None else dict(row)

    def list_model_offerings(self, *, limit: int, before: dict | None = None) -> list[dict]:
        """현재 제공 중이고 새 계산에 쓸 수 있는 판본을 최근 제공 순으로 반환한다."""
        filters = [
            "o.withdrawn_at IS NULL",
            "m.lifecycle_state='Active'",
            "m.registry_version IS NOT NULL",
        ]
        params: dict = {"limit": limit}
        if before:
            filters.append("(o.offered_at, o.model_revision_id) < (:created_at, :id)")
            params.update(created_at=before["created_at"], id=before["id"])
        with self.connection() as db:
            rows = db.execute(text(
                "SELECT o.description AS offering_description, o.offered_by, o.offered_at, m.* "
                "FROM model_offerings o JOIN model_revisions m ON m.id=o.model_revision_id "
                "WHERE " + " AND ".join(filters) + " "
                "ORDER BY o.offered_at DESC, o.model_revision_id DESC LIMIT :limit"
            ), params).mappings().all()
        items = []
        for row in rows:
            model = _model_item({key: value for key, value in row.items()
                                 if key not in ("offering_description", "offered_by", "offered_at")})
            # 페이지 커서는 제공 시각과 판본 ID를 사용한다.
            items.append({
                "id": model["id"], "created_at": row["offered_at"], "model": model,
                "description": row["offering_description"],
                "offered_by": row["offered_by"], "offered_at": row["offered_at"],
            })
        return items

    def offer_model(
        self, revision_id: str, *, description: str, actor_id: str, actor_type: str,
        owner_actor: str | None,
    ) -> dict:
        """모델 행을 잠근 상태에서 소유·사용 가능 상태를 다시 확인하고 제공 기록을 갱신한다.

        owner_actor가 주어지면 그 Actor가 소유한 판본만 제공한다. 관리자는 None을 전달한다.
        """
        with self.connection() as db:
            model = db.execute(
                text("SELECT * FROM model_revisions WHERE id=:id FOR UPDATE"), {"id": revision_id}
            ).mappings().first()
            if model is None or (owner_actor is not None and model["owner_actor"] != owner_actor):
                raise LookupError(revision_id)
            if model["lifecycle_state"] != "Active" or not model["registry_version"]:
                raise ModelOfferingUnavailable(
                    "Registry 등록이 끝난 활성 모델 판본만 제공할 수 있습니다."
                )
            at = now()
            db.execute(text(
                "INSERT INTO model_offerings"
                "(model_revision_id,description,offered_by,offered_at,withdrawn_by,withdrawn_at) "
                "VALUES(:id,:description,:actor,:at,NULL,NULL) "
                "ON CONFLICT (model_revision_id) DO UPDATE SET description=:description,"
                "offered_by=:actor,offered_at=:at,withdrawn_by=NULL,withdrawn_at=NULL"
            ), {"id": revision_id, "description": description, "actor": actor_id, "at": at})
            self._audit(
                db, actor_id=actor_id, actor_type=actor_type, action="model.offer",
                resource_type="model", resource_id=revision_id,
            )
        return self.get_model_offering(revision_id)

    def withdraw_model_offering(
        self, revision_id: str, *, actor_id: str, actor_type: str, owner_actor: str | None,
    ) -> dict | None:
        """새 계산에서만 제외한다. 이미 만든 사용자 결과와 제공 기록은 유지한다."""
        with self.connection() as db:
            model = db.execute(
                text("SELECT owner_actor FROM model_revisions WHERE id=:id FOR UPDATE"),
                {"id": revision_id},
            ).mappings().first()
            if model is None or (owner_actor is not None and model["owner_actor"] != owner_actor):
                raise LookupError(revision_id)
            updated = db.execute(text(
                "UPDATE model_offerings SET withdrawn_by=:actor,withdrawn_at=:at "
                "WHERE model_revision_id=:id AND withdrawn_at IS NULL"
            ), {"id": revision_id, "actor": actor_id, "at": now()})
            if updated.rowcount:
                self._audit(
                    db, actor_id=actor_id, actor_type=actor_type,
                    action="model.offer_withdrawn", resource_type="model", resource_id=revision_id,
                )
        return self.get_model_offering(revision_id)

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
            row = (
                db.execute(
                    text("SELECT * FROM decision_policy_revisions WHERE id=:id"),
                    {"id": revision_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        item = dict(row)
        item["decision_summary"] = json.loads(item["decision_summary"])
        return item

    def list_model_evaluations(
        self, revision_id: str, *, owner_actor: str | None, limit: int,
        before: dict | None = None,
    ) -> list[dict]:
        """평가의 실제 Scoring 참조로 모델을 대조하고 열람 가능한 판본만 반환한다."""
        filters = ["s.model_revision_id=:model"]
        params = {"model": revision_id, "limit": limit}
        if owner_actor is not None:
            filters.append("e.owner_actor=:owner")
            params["owner"] = owner_actor
        if before:
            filters.append("(e.created_at,e.id) < (:created_at,:id)")
            params.update(before)
        with self.connection() as db:
            rows = db.execute(text(
                "SELECT e.* FROM evaluation_revisions e "
                "JOIN scoring_results s ON s.id=e.scoring_result_id "
                "WHERE " + " AND ".join(filters) + " "
                "ORDER BY e.created_at DESC,e.id DESC LIMIT :limit"
            ), params).mappings().all()
        items = []
        for row in rows:
            item = dict(row)
            for field in ("decision_metrics", "ranking_metrics"):
                if item[field] is not None:
                    item[field] = json.loads(item[field])
            items.append(item)
        return items

    def get_evaluation_revision(self, revision_id: str) -> dict | None:
        """평가 조건·레이블 커버리지·지표의 불변 근거."""
        with self.connection() as db:
            row = (
                db.execute(
                    text("SELECT * FROM evaluation_revisions WHERE id=:id"),
                    {"id": revision_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        item = dict(row)
        for field in ("decision_metrics", "ranking_metrics"):
            if item[field] is not None:
                item[field] = json.loads(item[field])
        return item

    def get_explanation_result(self, result_id: str) -> dict | None:
        """개별 점수 설명의 근거·민감도 결과를 조회한다."""
        with self.connection() as db:
            row = (
                db.execute(
                    text("SELECT * FROM explanation_results WHERE id=:id"),
                    {"id": result_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        item = dict(row)
        item["output"] = json.loads(item["output"])
        return item
