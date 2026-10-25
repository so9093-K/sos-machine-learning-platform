"""Playground는 Dataset 없이 동작하되 모델 계약·점수 의미를 유지한다."""

import math
from types import SimpleNamespace

import pytest

from sos_platform.ml_playground import ordered_playground_input, run_playground_scoring


def _model(*, legacy=False):
    return {
        "id": "model-one", "owner_actor": "actor-1", "features": ["a", "b"],
        "runtime": {
            "feature_dtypes": ["Float64", "Int64"],
            "artifact_schema": None if legacy else "if_model_with_preprocessing_v1",
        },
    }


def test_playground_feature_order_and_input_validation():
    model = _model()
    assert ordered_playground_input(model, {"b": 2, "a": 1.5}) == [1.5, 2.0]
    with pytest.raises(ValueError, match="exactly match"):
        ordered_playground_input(model, {"a": 1})
    with pytest.raises(ValueError, match="exactly match"):
        ordered_playground_input(model, {"a": 1, "b": 2, "secret": 3})
    with pytest.raises(ValueError, match="numeric"):
        ordered_playground_input(model, {"a": True, "b": 2})
    with pytest.raises(ValueError, match="must be an integer"):
        ordered_playground_input(model, {"a": 1.5, "b": 2.4})
    with pytest.raises(ValueError, match="finite"):
        ordered_playground_input(model, {"a": math.inf, "b": 2})
    with pytest.raises(ValueError, match="exact float64"):
        ordered_playground_input(model, {"a": 2**53 + 1, "b": 2})
    assert math.isnan(ordered_playground_input(model, {"a": None, "b": 2})[0])
    with pytest.raises(ValueError, match="does not support missing"):
        ordered_playground_input(_model(legacy=True), {"a": None, "b": 2})


def test_playground_actual_adapter_and_no_dataset(monkeypatch, tmp_path):
    model = _model()
    seen = []
    class Adapter:
        def score_anomalies(self, matrix):
            seen.append(matrix.tolist())
            return (0.83,)
    monkeypatch.setattr(
        "sos_platform.ml_playground.load_trusted_isolation_forest",
        lambda current, scratch: Adapter(),
    )
    store = SimpleNamespace(
        get_model_revision=lambda model_id: model if model_id == model["id"] else None,
        home=tmp_path,
    )
    spec = {
        "model_revision_id": model["id"], "model_owner_actor": "actor-1",
        "features": {"b": 2, "a": 1.5},
    }
    output = run_playground_scoring(store, spec)
    assert seen == [[[1.5, 2.0]]]
    assert output == {
        "model_revision_id": model["id"],
        "anomaly_score": 0.83,
        "score_direction": "higher_is_more_anomalous",
        "score_scale": "negative_sklearn_score_samples",
        "feature_count": 2,
    }
    with pytest.raises(ValueError, match="unavailable"):
        run_playground_scoring(store, {**spec, "model_owner_actor": "wrong-actor"})
