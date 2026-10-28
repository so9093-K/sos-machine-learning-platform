"""SHAP 기여도의 점수 출력 일치·재현성·이전 Explanation 호환성."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sos_platform.ml_explanation import _permutation_shap_result, explain_scored_row
from sos_platform.schemas import MLExplanationRequest


class _LinearRuntime:
    def score_anomalies(self, matrix):
        values = np.asarray(matrix, dtype=np.float64)
        # 결측 행의 전처리도 설명 대상 점수 함수의 일부다.
        values = np.where(np.isnan(values), 0.0, values)
        return tuple((values @ np.asarray([0.5, -1.0, 2.0])).tolist())


def test_permutation_shap_reconstructs_actual_sos_score():
    runtime = _LinearRuntime()
    row = np.asarray([4.0, 3.0, 1.0])
    score = runtime.score_anomalies(row.reshape(1, -1))[0]
    args = (
        "scoring",
        {"id": "model", "features": ["first", "second", "third"]},
        4, row, [0.0, 0.0, 0.0], score, runtime,
    )
    first = _permutation_shap_result(*args)
    second = _permutation_shap_result(*args)
    assert first == second
    assert first["score"] == pytest.approx(1.0)
    assert first["baseline_score"] == pytest.approx(0.0)
    assert first["baseline_score"] + sum(
        entry["shap_value"] for entry in first["feature_contributions"]
    ) == pytest.approx(score, abs=1e-7)
    assert [e["shap_value"] for e in first["feature_contributions"]] == pytest.approx(
        [2.0, -3.0, 2.0], abs=1e-7
    )
    assert first["model_output"] == "negative_sklearn_score_samples"
    assert first["baseline_row_count"] == 1
    assert first["additive_attribution"] is True



def test_permutation_shap_reconstructs_real_isolation_forest_score():
    """설명 모델이 선형 함수가 아니라 실제 SOS IF 점수인 경우도 검증한다."""
    from sos_platform.ml_isolation_forest import (
        IsolationForestConfig, train_isolation_forest,
    )

    rng = np.random.default_rng(24)
    training = rng.normal(size=(70, 44))
    model = train_isolation_forest(
        training, IsolationForestConfig(n_estimators=24, random_state=42)
    )
    values = training[54].copy()
    values[0] += 4.0
    reference = np.median(training, axis=0).tolist()
    score = model.score_anomalies(values.reshape(1, -1))[0]
    result = _permutation_shap_result(
        "scoring", {"id": "model", "features": [f"f{i}" for i in range(44)]},
        4, values, reference, score, model,
    )
    assert len(result["feature_contributions"]) == 44
    assert result["baseline_score"] == pytest.approx(
        model.score_anomalies(np.asarray(reference).reshape(1, 44))[0], abs=1e-7
    )
    assert result["reconstructed_score"] == pytest.approx(score, abs=1e-7)
    assert abs(result["additivity_residual"]) < 1e-7

def test_permutation_shap_includes_missing_value_preprocessing():
    runtime = _LinearRuntime()
    row = np.asarray([float("nan"), 2.0, 2.0])
    score = runtime.score_anomalies(row.reshape(1, -1))[0]
    result = _permutation_shap_result(
        "s", {"id": "m", "features": ["x", "y", "z"]},
        0, row, [0.0, 0.0, 0.0], score, runtime,
    )
    assert result["feature_contributions"][0]["missing_imputed"] is True
    assert result["feature_contributions"][0]["actual_value"] is None
    assert result["reconstructed_score"] == pytest.approx(score, abs=1e-7)


def test_explanation_method_contract_and_existing_default(monkeypatch, tmp_path: Path):
    assert MLExplanationRequest(scoring_result_id="s", row_index=0).method == (
        "median_reference_feature_replacement"
    )
    assert MLExplanationRequest(
        scoring_result_id="s", row_index=0, method="permutation_shap_median_v1"
    ).method == "permutation_shap_median_v1"
    with pytest.raises(ValueError):
        MLExplanationRequest(scoring_result_id="s", row_index=0, method="unsupported")

    from sos_platform import ml_explanation
    row = {"anomaly_score": 3.0, "features": {"a": 2.0, "b": 1.0, "c": 1.5}}
    monkeypatch.setattr(ml_explanation, "_verified_scoring_row", lambda *_: row)
    monkeypatch.setattr(ml_explanation, "load_trusted_isolation_forest",
                        lambda *_: _LinearRuntime())
    scoring = {"id": "s", "owner_actor": "owner", "model_revision_id": "m",
               "score_summary": {}}
    model = {"id": "m", "features": ["a", "b", "c"],
             "runtime": {"feature_reference_medians": [0.0, 0.0, 0.0]}}
    store = SimpleNamespace(
        get_scoring_result=lambda _: scoring, get_model_revision=lambda _: model,
        home=tmp_path,
    )
    base = {"scoring_result_id": "s", "source_owner_actor": "owner", "row_index": 0}
    original = explain_scored_row(store, base)
    shap = explain_scored_row(store, {**base, "method": "permutation_shap_median_v1"})
    assert original["method"] == "median_reference_feature_replacement"
    assert original["additive_attribution"] is False
    assert shap["method"] == "permutation_shap_median_v1"
    assert shap["additive_attribution"] is True
    assert shap["score"] == original["score"] == 3.0
    scoring["dataset_id"] = "dataset"
    scoring["dataset_version"] = "v1"
    scoring["score_summary"] = {"observation_identity": {
        "kind": "opaque_uuid4", "dataset_id": "dataset", "dataset_version": "v1",
        "source_column": "sos_observation_id",
    }}
    row["observation_id"] = "18c4be49-18c4-4f32-9894-8c3bfabbd008"
    linked = explain_scored_row(store, {**base, "method": "permutation_shap_median_v1"})
    assert linked["source_observation"] == {
        "dataset_id": "dataset", "dataset_version": "v1",
        "id_column": "sos_observation_id",
        "observation_id": row["observation_id"],
    }


def test_explanation_request_requires_exactly_one_input_source():
    from pydantic import ValidationError
    assert MLExplanationRequest(playground_job_id="job-1").row_index is None
    assert MLExplanationRequest(scoring_result_id="scoring-1", row_index=0).playground_job_id is None
    for invalid in (
        {},
        {"scoring_result_id": "s"},
        {"playground_job_id": "p", "row_index": 0},
        {"playground_job_id": "p", "scoring_result_id": "s", "row_index": 0},
    ):
        with pytest.raises(ValidationError):
            MLExplanationRequest(**invalid)


def test_playground_explanation_reuses_real_score_and_methods(monkeypatch, tmp_path):
    from sos_platform import ml_explanation
    runtime = _LinearRuntime()
    model = {
        "id": "model", "owner_actor": "actor-1", "lifecycle_state": "Active",
        "features": ["first", "second", "third"],
        "runtime": {
            "feature_dtypes": ["Float64", "Float64", "Float64"],
            "feature_reference_medians": [0.0, 0.0, 0.0],
            "artifact_schema": "if_model_with_preprocessing_v1",
        },
    }
    features = {"third": 1.0, "first": 4.0, "second": 3.0}
    expected = runtime.score_anomalies(np.asarray([[4.0, 3.0, 1.0]]))[0]
    job = {
        "id": "pg-job", "kind": "ml_playground", "status": "Succeeded",
        "owner_actor": "actor-1",
        "spec": {
            "model_revision_id": "model", "model_owner_actor": "actor-1",
            "features": features,
        },
        "result": {
            "model_revision_id": "model",
            "anomaly_score": expected,
            "score_direction": "higher_is_more_anomalous",
            "score_scale": "negative_sklearn_score_samples",
            "feature_count": 3,
        },
    }
    store = SimpleNamespace(
        get_job=lambda _: job,
        get_model_revision=lambda _: model,
        home=tmp_path,
    )
    monkeypatch.setattr(ml_explanation, "load_trusted_isolation_forest", lambda *_: runtime)
    spec = {"playground_job_id": "pg-job", "source_owner_actor": "actor-1"}
    shap = ml_explanation.explain_scored_row(
        store, {**spec, "method": "permutation_shap_median_v1"})
    sensitivity = ml_explanation.explain_scored_row(
        store, {**spec, "method": "median_reference_feature_replacement"})
    assert shap["playground_job_id"] == sensitivity["playground_job_id"] == "pg-job"
    assert shap["scoring_result_id"] is None and shap["row_index"] is None
    assert shap["reconstructed_score"] == pytest.approx(expected, abs=1e-7)
    assert shap["baseline_score"] + sum(
        x["shap_value"] for x in shap["feature_contributions"]
    ) == pytest.approx(expected)
    assert len(sensitivity["feature_sensitivities"]) == 3
    assert sensitivity["feature_sensitivities"][0]["anomaly_score_delta"] == pytest.approx(2.0)
    assert sensitivity["additive_attribution"] is False

    for invalid in (
        {**job, "status": "Running"},
        {**job, "kind": "ml_score"},
        {**job, "owner_actor": "another"},
        {**job, "result": {**job["result"], "anomaly_score": expected + 1}},
        {**job, "result": {**job["result"], "model_revision_id": "another"}},
        {**job, "result": {**job["result"], "score_scale": "incompatible"}},
        {**job, "result": {**job["result"], "score_direction": "lower_is_more_anomalous"}},
        {**job, "result": {**job["result"], "feature_count": 2}},
    ):
        store.get_job = lambda _, item=invalid: item
        with pytest.raises(ValueError):
            ml_explanation.explain_scored_row(
                store, {**spec, "method": "permutation_shap_median_v1"})


def test_playground_explanation_api_checks_source_authority_and_status(tmp_path, monkeypatch):
    """Playground 설명은 요청자가 접근 가능한 성공한 원본 Job만 받는다."""
    import hashlib
    from fastapi.testclient import TestClient
    from sos_platform.api import create_app
    from sos_platform.security import Actor, IdentitySettings
    from sos_platform.store import Store

    owner_token = "explanation-owner-credential-verified-2026"
    other_token = "explanation-other-credential-verified-2026"
    identity = IdentitySettings("token", {
        hashlib.sha256(token.encode()).hexdigest():
            Actor(actor, "human", frozenset({"researcher"}))
        for token, actor in ((owner_token, "owner"), (other_token, "other"))
    })
    source = {
        "id": "playground-success", "kind": "ml_playground", "status": "Succeeded",
        "owner_actor": "owner",
        "spec": {"model_revision_id": "model", "model_owner_actor": "owner",
                 "features": {"first": 2}},
        "result": {"anomaly_score": 0.5},
    }
    model = {"id": "model", "owner_actor": "owner", "lifecycle_state": "Active"}
    calls = []
    monkeypatch.setattr(Store, "get_job", lambda self, _: source)
    monkeypatch.setattr(Store, "get_model_revision", lambda self, _: model)

    def create_job(self, **kwargs):
        calls.append(kwargs)
        return {
            "id": "explanation-job", "kind": kwargs["kind"],
            "dataset_id": kwargs["dataset_id"], "spec": kwargs["spec"],
            "status": "Queued", "cancel_requested": False,
            "result": None, "error": None,
            "request_id": kwargs.get("request_id"),
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z", "attempts": [],
        }

    monkeypatch.setattr(Store, "create_job", create_job)
    client = TestClient(create_app(tmp_path, identity=identity))
    headers = {"Authorization": f"Bearer {owner_token}",
               "Idempotency-Key": "explain-test-id-1"}
    payload = {"playground_job_id": source["id"],
               "method": "permutation_shap_median_v1"}
    response = client.post("/api/ml/explanations/jobs", json=payload, headers=headers)
    assert response.status_code == 202, response.text
    assert calls[-1]["dataset_id"] is None
    assert calls[-1]["spec"] == {
        "playground_job_id": source["id"], "source_owner_actor": "owner",
        "method": "permutation_shap_median_v1",
    }
    assert client.post(
        "/api/ml/explanations/jobs", json=payload,
        headers={**headers, "Authorization": f"Bearer {other_token}"},
    ).status_code == 404
    for bad_status in ("Queued", "Running", "Failed", "Cancelled"):
        source["status"] = bad_status
        assert client.post(
            "/api/ml/explanations/jobs", json=payload, headers=headers
        ).status_code == 409
    source["status"] = "Succeeded"
    for bad_score in (float("nan"), float("inf"), True):
        source["result"] = {"anomaly_score": bad_score}
        assert client.post(
            "/api/ml/explanations/jobs", json=payload, headers=headers
        ).status_code == 409
    source["result"] = {"anomaly_score": 0.5}
    model["lifecycle_state"] = "Deleted"
    assert client.post(
        "/api/ml/explanations/jobs", json=payload, headers=headers
    ).status_code == 409
    model["lifecycle_state"] = "Active"
    assert client.post(
        "/api/ml/explanations/jobs",
        json={**payload, "scoring_result_id": "invalid", "row_index": 0},
        headers=headers,
    ).status_code == 422
