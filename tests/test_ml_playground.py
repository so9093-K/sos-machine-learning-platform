"""Playground는 Dataset 없이 동작하되 모델 계약·점수 의미를 유지한다."""

import math
from types import SimpleNamespace

import pytest

from sos_platform.ml_input import ordered_playground_input
from sos_platform.ml_playground import run_playground_scoring


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
    with pytest.raises(ValueError, match="dtype range"):
        ordered_playground_input({**model, "runtime": {
            **model["runtime"], "feature_dtypes": ["Float64", "Int8"],
        }}, {"a": 1.5, "b": 128})
    with pytest.raises(ValueError, match="dtype range"):
        ordered_playground_input({**model, "runtime": {
            **model["runtime"], "feature_dtypes": ["Float32", "Int64"],
        }}, {"a": 1e39, "b": 2})
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


def test_playground_score_matches_trained_isolation_forest(monkeypatch, tmp_path):
    """동일 모델을 통한 Playground 계산과 기존 모델 어댑터의 점수가 일치한다."""
    import numpy as np
    from sklearn.ensemble import IsolationForest
    from sos_platform.ml_if_config import IsolationForestConfig
    from sos_platform.ml_isolation_forest import TrainedIsolationForest

    train = np.array([[1.0, 2.0], [2.0, 5.0], [3.0, 8.0], [50.0, 100.0]])
    estimator = IsolationForest(n_estimators=12, random_state=42).fit(train)
    adapter = TrainedIsolationForest(
        estimator, IsolationForestConfig(n_estimators=12), n_features=2,
    )
    monkeypatch.setattr(
        "sos_platform.ml_playground.load_trusted_isolation_forest",
        lambda model, scratch: adapter,
    )
    model = _model(legacy=True)
    store = SimpleNamespace(get_model_revision=lambda _: model, home=tmp_path)
    values = {"b": 5, "a": 2.0}
    result = run_playground_scoring(store, {
        "model_revision_id": model["id"], "model_owner_actor": model["owner_actor"],
        "features": values,
    })
    assert result["anomaly_score"] == adapter.score_anomalies(
        np.array([[2.0, 5.0]])
    )[0]


def test_playground_api_request_disallows_coerced_feature_types():
    """API 앞단에서 Boolean이나 숫자 문자열을 임의로 수치화하지 않는다."""
    from pydantic import ValidationError
    from sos_platform.schemas import MLPlaygroundRequest

    assert MLPlaygroundRequest(model_revision_id="m", features={"a": 2}).features == {"a": 2}
    for value in ("2", True):
        with pytest.raises(ValidationError):
            MLPlaygroundRequest(model_revision_id="m", features={"a": value})



def test_model_filtered_history_runs_filter_before_pagination():
    """Job 저장소의 SQL 모델 필터는 LIMIT 앞에서 적용되고 Actor 경계도 유지한다."""
    from contextlib import nullcontext

    from sos_platform.store import Store

    executed = []

    class EmptyResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeConnection:
        def execute(self, query, params):
            executed.append((str(query), params))
            return EmptyResult()

    fake = SimpleNamespace(connection=lambda: nullcontext(FakeConnection()))
    assert Store.jobs(fake, kind="ml_playground", actor_id="actor-1",
                      model_revision_id="model-1", limit=10) == []
    statement, params = executed[0]
    assert "spec::jsonb ->> 'model_revision_id'" in statement
    assert "owner_actor=:actor_id" in statement
    assert "kind=:kind" in statement
    assert statement.index("model_revision_id") < statement.index("LIMIT")
    assert params["model_revision_id"] == "model-1"
    assert params["actor_id"] == "actor-1"



def test_playground_model_history_index_matches_migration_predicate():
    """모델별 이력 조회용 부분 인덱스가 PostgreSQL 정본 메타데이터에 포함된다."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    from sos_platform.schema_db import jobs

    item = next(
        index for index in jobs.indexes
        if index.name == "ix_jobs_playground_model_actor_created"
    )
    definition = str(CreateIndex(item).compile(dialect=postgresql.dialect()))
    assert "spec::jsonb ->> 'model_revision_id'" in definition
    assert "WHERE kind = 'ml_playground'" in definition
    assert "created_at DESC" in definition
