"""모델 평가 조회의 실제 참조·소유권·페이지 경계를 확인한다."""

import hashlib
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from sos_platform.api import create_app
from sos_platform.security import Actor, IdentitySettings
from sos_platform.store import Store


def _evaluation(identifier, *, owner="supplier", scoring="score-a"):
    return {
        "id": identifier, "job_id": "job", "attempt_id": "attempt",
        "scoring_result_id": scoring, "policy_revision_id": None,
        "label_coverage": "none", "decision_metrics": {"score_count": 48},
        "ranking_metrics": None, "owner_actor": owner, "created_at": "2026-10-29T00:00:00Z",
    }


def test_model_evaluations_join_scoring_and_keep_owner_and_cursor_scope():
    """SQLite에서 조회 SQL을 실행해 다른 모델·다른 소유자의 평가 혼입을 막는다."""
    store = object.__new__(Store)
    store.engine = create_engine("sqlite://")
    try:
        with store.connection() as db:
            db.execute(text("CREATE TABLE scoring_results (id TEXT, model_revision_id TEXT)"))
            db.execute(text(
                "CREATE TABLE evaluation_revisions (id TEXT, scoring_result_id TEXT, "
                "owner_actor TEXT, created_at TEXT, decision_metrics TEXT, ranking_metrics TEXT)"
            ))
            db.execute(text("INSERT INTO scoring_results VALUES ('score-a','model-a'),('score-b','model-b')"))
            rows = [
                _evaluation("a"), _evaluation("b"), _evaluation("c", owner="other"),
                _evaluation("d", scoring="score-b"),
            ]
            for row in rows:
                db.execute(text(
                    "INSERT INTO evaluation_revisions VALUES "
                    "(:id,:scoring_result_id,:owner_actor,:created_at,:decision_metrics,:ranking_metrics)"
                ), {**row, "decision_metrics": json.dumps(row["decision_metrics"])})
        own = store.list_model_evaluations("model-a", owner_actor="supplier", limit=1)
        assert [row["id"] for row in own] == ["b"]
        assert own[0]["decision_metrics"] == {"score_count": 48}
        older = store.list_model_evaluations("model-a", owner_actor="supplier", limit=5,
                                             before={"created_at": own[0]["created_at"], "id": "b"})
        assert [row["id"] for row in older] == ["a"]
        all_visible = store.list_model_evaluations("model-a", owner_actor=None, limit=5)
        assert [row["id"] for row in all_visible] == ["c", "b", "a"]
        assert store.list_model_evaluations("no-model", owner_actor=None, limit=5) == []
    finally:
        store.engine.dispose()


def test_model_evidence_api_preserves_private_evaluation_access(tmp_path, monkeypatch):
    """제공받은 모델의 본인 평가만 조회하고 공급자·다른 모델의 근거는 노출하지 않는다."""
    subjects = {
        "supplier-token-with-enough-entropy": Actor("supplier", "service", frozenset({"automation"})),
        "consumer-token-with-enough-entropy": Actor("consumer", "human", frozenset({"researcher"})),
        "administrator-token-with-enough-entropy": Actor("admin", "human", frozenset({"administrator"})),
    }
    identity = IdentitySettings("token", {
        hashlib.sha256(token.encode()).hexdigest(): actor for token, actor in subjects.items()
    })
    monkeypatch.setattr(Store, "record_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(Store, "get_model_revision", lambda self, mid: {
        "id": mid, "owner_actor": "supplier",
    } if mid == "model-a" else None)
    monkeypatch.setattr(Store, "get_model_offering", lambda *args: {"withdrawn_at": None})
    queries = []
    rows = [_evaluation(f"00000000-0000-4000-8000-{i:012d}") for i in (3, 2, 1)]
    consumer_evaluation = _evaluation("00000000-0000-4000-8000-000000000004", owner="consumer")

    def listed(self, mid, **kwargs):
        queries.append((mid, kwargs))
        if kwargs["owner_actor"] == "consumer":
            return [consumer_evaluation]
        return rows if kwargs["before"] is None else rows[2:]

    monkeypatch.setattr(Store, "list_model_evaluations", listed)
    client = TestClient(create_app(tmp_path, identity=identity))
    supplier, consumer, admin = [{"Authorization": f"Bearer {token}"} for token in subjects]
    path = "/api/ml/models/model-a/evaluations"
    assert client.get(path).status_code == 401
    assert queries == []
    own_result = client.get(path, headers=consumer)
    assert own_result.status_code == 200
    assert [row["id"] for row in own_result.json()["items"]] == [consumer_evaluation["id"]]
    assert queries[-1][1]["owner_actor"] == "consumer"
    monkeypatch.setattr(Store, "get_model_offering", lambda *args: None)
    count = len(queries)
    assert client.get(path, headers=consumer).status_code == 404
    assert len(queries) == count
    first = client.get(path, params={"limit": 2}, headers=supplier)
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    assert queries[-1] == ("model-a", {"owner_actor": "supplier", "limit": 3, "before": None})
    second = client.get(path, params={"limit": 2, "cursor": first.json()["next_cursor"]}, headers=supplier)
    assert second.status_code == 200
    assert second.json()["next_cursor"] is None
    assert queries[-1][1]["before"]["id"] == rows[1]["id"]
    assert client.get(path, headers=admin).status_code == 200
    assert queries[-1][1]["owner_actor"] is None
    rows.clear()
    assert client.get(path, headers=supplier).json() == {"items": [], "next_cursor": None}
    assert client.get("/api/ml/models/missing/evaluations", headers=supplier).status_code == 404
    assert client.get(path, params={"limit": 51}, headers=supplier).status_code == 422
