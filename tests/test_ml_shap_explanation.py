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
        {"id": "scoring"},
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
        {"id": "scoring"}, {"id": "model", "features": [f"f{i}" for i in range(44)]},
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
        {"id": "s"}, {"id": "m", "features": ["x", "y", "z"]},
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
