"""Calibration에 산정한 정책을 별개 Scoring에 적용하는 계약을 검증한다."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from sos_platform import ml_decision


class LocalScoringObjects:
    """테스트 객체 바이트·무결성·Attempt별 위치를 검증한다."""

    def __init__(self, files: dict[str, Path]):
        self.files = files

    def location(self, key: str) -> str:
        return f"mock://{key}"

    def get(self, key: str, destination: Path, *, expected_sha256: str) -> None:
        from shutil import copyfile

        blob = self.files[key]
        if hashlib.sha256(blob.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError("Scoring artifact integrity mismatch")
        copyfile(blob, destination)


@pytest.fixture
def scoring_context(monkeypatch, tmp_path):
    """서로 다른 Dataset으로부터 생성된 보정·평가 점수 판본."""
    (tmp_path / "cache").mkdir()
    files: dict[str, Path] = {}
    storage = LocalScoringObjects(files)
    monkeypatch.setattr(ml_decision, "ObjectStore", lambda: storage)

    def create(name: str, scores: list[float], *, model: str = "model-1"):
        key = f"results/{name}/{name}-attempt.scores.parquet"
        file = tmp_path / f"{name}.parquet"
        pl.DataFrame({
            "row_index": list(range(len(scores))),
            "anomaly_score": scores,
        }).write_parquet(file)
        files[key] = file
        return {
            "id": name,
            "job_id": name,
            "attempt_id": f"{name}-attempt",
            "location": storage.location(key),
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "row_count": len(scores),
            "model_revision_id": model,
            "owner_actor": "owner-a",
            "dataset_id": f"dataset-{name}",
            "dataset_version": f"version-{name}",
            "feature_names": ["connections", "writes"],
            "score_direction": "higher_is_more_anomalous",
            "score_summary": {"score_scale": "negative_sklearn_score_samples"},
            "ranking_metrics": None,
        }

    calibration = create("calibration", [0.1, 0.2, 0.5, 0.6])
    test = create("test", [0.1, 0.3, 0.7, 0.8])
    scoring = {s["id"]: s for s in (calibration, test)}
    policies: dict[str, dict] = {}
    store = SimpleNamespace(
        home=tmp_path,
        get_scoring_result=lambda rid: scoring.get(rid),
        get_decision_policy_revision=lambda rid: policies.get(rid),
    )
    return store, calibration, test, scoring, policies


def create_policy(store, calibration):
    result = ml_decision.apply_scoring_policy(store, {
        "scoring_result_id": calibration["id"],
        "source_owner_actor": "owner-a",
        "mode": "quantile",
        "alert_fraction": 0.25,
    })
    return {
        **result,
        "id": "policy-1",
        "owner_actor": "owner-a",
    }


def test_calibration_quantile_applied_unchanged_to_distinct_test(scoring_context):
    store, calibration, test, _, policies = scoring_context
    policy = create_policy(store, calibration)
    policies["policy-1"] = policy
    result = ml_decision.evaluate_scoring_policy(store, {
        "scoring_result_id": test["id"],
        "source_owner_actor": "owner-a",
        "policy_revision_id": "policy-1",
        "policy_owner_actor": "owner-a",
        "reference_owner_actor": "owner-a",
    })
    metrics = result["decision_metrics"]
    assert policy["threshold"] == pytest.approx(0.525)
    assert metrics["threshold"] == policy["threshold"]
    assert metrics["predicted_anomalies"] == 2
    assert metrics["alert_rate"] == 0.5
    assert metrics["policy_reference_scoring_result_id"] == calibration["id"]
    assert metrics["policy_reference_relation"] == (
        "different_scoring_result_disjointness_unverified"
    )
    assert metrics["independent_validation_confirmed"] is False
    assert "precision" not in metrics
    # Test 값 변경은 이미 생성된 정책의 임계값을 다시 산정하지 않는다.
    assert policies["policy-1"]["threshold"] == pytest.approx(0.525)


@pytest.mark.parametrize("changed", [
    "model_revision_id",
    "score_direction",
    "feature_names",
    "score_scale",
    "reference_missing",
    "reference_owner",
    "policy_owner",
])
def test_invalid_cross_scoring_policy_rejected(scoring_context, changed):
    store, calibration, test, scoring, policies = scoring_context
    policy = create_policy(store, calibration)
    policies["policy-1"] = policy
    if changed == "reference_missing":
        scoring.pop("calibration")
    elif changed == "reference_owner":
        calibration["owner_actor"] = "owner-b"
    elif changed == "policy_owner":
        policy["owner_actor"] = "owner-b"
    elif changed == "score_scale":
        test["score_summary"] = {"score_scale": "some_other_scale"}
    elif changed == "feature_names":
        test["feature_names"] = ["writes", "connections"]
    elif changed == "model_revision_id":
        test["model_revision_id"] = "model-2"
    else:
        test["score_direction"] = "lower_is_more_anomalous"
    with pytest.raises(ValueError):
        ml_decision.evaluate_scoring_policy(store, {
            "scoring_result_id": test["id"],
            "source_owner_actor": "owner-a",
            "policy_revision_id": "policy-1",
            "policy_owner_actor": "owner-a",
            "reference_owner_actor": "owner-a",
        })


def test_same_scoring_policy_marks_reference_relation_not_independent(scoring_context):
    store, calibration, _, _, policies = scoring_context
    policies["policy-1"] = create_policy(store, calibration)
    result = ml_decision.evaluate_scoring_policy(store, {
        "scoring_result_id": calibration["id"],
        "source_owner_actor": "owner-a",
        "policy_revision_id": "policy-1",
    })
    metrics = result["decision_metrics"]
    assert metrics["policy_reference_relation"] == "same_scoring_result"
    assert metrics["independent_validation_confirmed"] is False


def test_policy_is_optional_for_unlabeled_evaluation(scoring_context):
    store, _, test, _, _ = scoring_context
    result = ml_decision.evaluate_scoring_policy(store, {
        "scoring_result_id": test["id"],
        "source_owner_actor": "owner-a",
        "policy_revision_id": None,
    })
    assert result["decision_metrics"]["threshold"] is None
    assert "policy_reference_relation" not in result["decision_metrics"]


def test_http_evaluation_validates_policy_reference_access_and_model(
    monkeypatch, scoring_context, tmp_path
):
    """평가 제출 단계에서도 정책·보정 점수 접근 권한을 따로 검사한다."""
    from unittest.mock import Mock

    from fastapi.testclient import TestClient

    from sos_platform import api
    from sos_platform.security import Actor, IdentitySettings

    _, calibration, test, scoring, policies = scoring_context
    policies["policy-1"] = {
        **create_policy(scoring_context[0], calibration),
        "owner_actor": "owner-a",
    }
    fake_store = Mock()
    fake_store.get_scoring_result.side_effect = lambda rid: scoring.get(rid)
    fake_store.get_decision_policy_revision.side_effect = lambda rid: policies.get(rid)

    def created(**kwargs):
        return {
            "id": "eval-job", "kind": kwargs["kind"], "dataset_id": kwargs["dataset_id"],
            "spec": kwargs["spec"], "status": "Queued", "cancel_requested": False,
            "result": None, "error": None, "attempts": [],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }

    fake_store.create_job.side_effect = created
    monkeypatch.setattr(api, "Store", lambda home: fake_store)
    app = api.create_app(tmp_path, identity=IdentitySettings("development", {}))
    actor = Actor("owner-a", "human", frozenset({"researcher"}))
    app.dependency_overrides[api.CREATE_JOB_DEP.dependency] = lambda: actor
    with TestClient(app) as client:
        response = client.post("/api/ml/evaluations/jobs", json={
            "scoring_result_id": test["id"], "policy_revision_id": "policy-1",
        })
        assert response.status_code == 202, response.text
        spec = fake_store.create_job.call_args.kwargs["spec"]
        assert spec["policy_owner_actor"] == "owner-a"
        assert spec["reference_owner_actor"] == "owner-a"

        calibration["owner_actor"] = "owner-b"
        denied = client.post("/api/ml/evaluations/jobs", json={
            "scoring_result_id": test["id"], "policy_revision_id": "policy-1",
        })
        assert denied.status_code == 404
        calibration["owner_actor"] = "owner-a"

        test["model_revision_id"] = "unrelated-model"
        incompatible = client.post("/api/ml/evaluations/jobs", json={
            "scoring_result_id": test["id"], "policy_revision_id": "policy-1",
        })
        assert incompatible.status_code == 422
