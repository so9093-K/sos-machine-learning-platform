"""모델과 독립 Scoring Result의 영속 조회 계약."""

import json
from collections.abc import Mapping

from sqlalchemy import text

from .errors import ERRORS, InvalidRequestError
from .model_use import GROUP_ARCHIVED, GROUP_JOIN, USABLE_SQL, locked_model, use_blocked
from .store_administration import now

MODEL_JSON_FIELDS = ("features", "params", "training_score_summary", "runtime")


class ModelOfferingUnavailable(Exception):
    """제공할 수 없는 모델 상태. 사유 문장은 사용자 안내에 그대로 쓴다."""


def _model_item(row) -> dict:
    item = dict(row)
    for field in MODEL_JSON_FIELDS:
        item[field] = json.loads(item[field])
    # SOS가 학습한 판본은 출처 기록이 없다. 출처는 학습 Job·Dataset이다.
    item["source"] = json.loads(item["source"]) if item.get("source") else None
    return item


# 공개 평가 근거로 보이는 판정 집계. 관측값·Dataset이 아니라 집계 수치만 공개한다.
PUBLISHED_DECISION_FIELDS = (
    "score_count", "threshold", "alert_rate", "predicted_anomalies", "evaluation_kind",
    "true_positives", "false_positives", "false_negatives", "precision", "recall", "f1",
)


def check_publishable_evaluation(
    row: Mapping | None, revision_id: str, actor_id: str, privileged: bool,
) -> None:
    """공개할 수 있는 평가: 이 판본의 Scoring으로 만든 평가이고, 요청 계정이 열람할 수 있어야 한다."""
    if row is None or row["model_revision_id"] != revision_id:
        raise InvalidRequestError("이 모델 판본의 점수로 만든 평가만 공개할 수 있습니다.")
    if not privileged and row["owner_actor"] != actor_id:
        raise InvalidRequestError("이 계정이 만든 평가만 공개할 수 있습니다.")


def published_evaluation(row: Mapping) -> dict:
    """소비자에게 보이는 공개 평가 요약. 평가에 쓴 Dataset·Scoring 식별자는 포함하지 않는다."""
    decision = json.loads(row["decision_metrics"])
    return {
        "evaluation_revision_id": row["evaluation_revision_id"],
        "note": row["note"],
        "published_at": row["published_at"],
        "evaluated_at": row["created_at"],
        "label_coverage": row["label_coverage"],
        "policy": None if row["policy_mode"] is None else {
            "mode": row["policy_mode"], "threshold": row["policy_threshold"],
            "alert_fraction": row["policy_alert_fraction"],
        },
        "decision_metrics": {key: decision.get(key) for key in PUBLISHED_DECISION_FIELDS},
        "ranking_metrics": json.loads(row["ranking_metrics"]) if row["ranking_metrics"] else None,
    }


class ModelStoreMixin:
    """모델 및 점수 결과 게시 자체는 Job 종료 트랜잭션이 수행한다."""

    def get_model_revision(self, revision_id: str) -> dict | None:
        """실행 파일을 로딩하지 않고 모델 메타데이터만 조회한다."""
        with self.connection() as db:
            row = db.execute(text(
                f"SELECT m.*, {GROUP_ARCHIVED} FROM model_revisions m {GROUP_JOIN} WHERE m.id=:id"
            ), {"id": revision_id}).mappings().first()
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
        filters = ["o.withdrawn_at IS NULL", USABLE_SQL]
        params: dict = {"limit": limit}
        if before:
            filters.append("(o.offered_at, o.model_revision_id) < (:created_at, :id)")
            params.update(created_at=before["created_at"], id=before["id"])
        with self.connection() as db:
            rows = db.execute(text(
                f"SELECT o.description AS offering_description, o.offered_by, o.offered_at, m.*, "
                f"{GROUP_ARCHIVED} FROM model_offerings o JOIN model_revisions m "
                f"ON m.id=o.model_revision_id {GROUP_JOIN} "
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
            model = locked_model(db, revision_id, for_update=True)
            if model is None or (owner_actor is not None and model["owner_actor"] != owner_actor):
                raise LookupError(revision_id)
            blocked = use_blocked(model)
            if blocked:
                raise ModelOfferingUnavailable(ERRORS[blocked].message)
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

    def set_offering_evaluations(
        self, revision_id: str, items: list[dict], *, actor_id: str, actor_type: str,
        owner_actor: str | None, privileged: bool,
    ) -> list[dict]:
        """공개할 평가 목록을 통째로 바꾼다. 평가마다 같은 판본·열람 권한을 확인한다.

        owner_actor가 주어지면 그 Actor가 소유한 판본만 바꾼다. 관리자는 None을 전달한다.
        """
        with self.connection() as db:
            model = db.execute(
                text("SELECT owner_actor FROM model_revisions WHERE id=:id FOR UPDATE"),
                {"id": revision_id},
            ).mappings().first()
            if model is None or (owner_actor is not None and model["owner_actor"] != owner_actor):
                raise LookupError(revision_id)
            for item in items:
                row = db.execute(text(
                    "SELECT e.owner_actor, s.model_revision_id FROM evaluation_revisions e "
                    "JOIN scoring_results s ON s.id=e.scoring_result_id WHERE e.id=:id"
                ), {"id": item["evaluation_revision_id"]}).mappings().first()
                check_publishable_evaluation(row, revision_id, actor_id, privileged)
            db.execute(text("DELETE FROM model_offering_evaluations WHERE model_revision_id=:id"),
                       {"id": revision_id})
            at = now()
            for position, item in enumerate(items):
                db.execute(text(
                    "INSERT INTO model_offering_evaluations(model_revision_id,evaluation_revision_id,"
                    "note,position,published_by,published_at) VALUES(:id,:evaluation,:note,:position,"
                    ":actor,:at)"
                ), {"id": revision_id, "evaluation": item["evaluation_revision_id"],
                    "note": item["note"], "position": position, "actor": actor_id, "at": at})
            self._audit(
                db, actor_id=actor_id, actor_type=actor_type, action="model.offer_evaluations",
                resource_type="model", resource_id=revision_id, reason=f"evaluations={len(items)}",
            )
        return self.list_offering_evaluations(revision_id)

    def list_offering_evaluations(self, revision_id: str) -> list[dict]:
        """공급자가 공개한 평가 요약을 공개한 순서대로 반환한다."""
        with self.connection() as db:
            rows = db.execute(text(
                "SELECT o.evaluation_revision_id, o.note, o.published_at, e.created_at, "
                "e.label_coverage, e.decision_metrics, e.ranking_metrics, p.mode AS policy_mode, "
                "p.threshold AS policy_threshold, p.alert_fraction AS policy_alert_fraction "
                "FROM model_offering_evaluations o "
                "JOIN evaluation_revisions e ON e.id=o.evaluation_revision_id "
                "LEFT JOIN decision_policy_revisions p ON p.id=e.policy_revision_id "
                "WHERE o.model_revision_id=:id ORDER BY o.position"
            ), {"id": revision_id}).mappings().all()
        return [published_evaluation(row) for row in rows]

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

    def scoring_observation_parts(self, result_id: str) -> list[dict]:
        """Scoring 결과의 관측값 색인 조각(order·position). 색인이 아직 없으면 빈 목록이다."""
        with self.connection() as db:
            return [dict(row) for row in db.execute(text(
                "SELECT * FROM scoring_observation_parts WHERE scoring_result_id=:id "
                "ORDER BY kind, part"
            ), {"id": result_id}).mappings()]

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
