"""실제 scikit-learn Isolation Forest 학습/점수 계약."""

import numpy as np
import pytest

from sos_platform.ml_isolation_forest import IsolationForestConfig, train_isolation_forest


def test_isolation_forest_training_and_score_are_reproducible() -> None:
    random = np.random.default_rng(17)
    normal = random.normal(size=(120, 2))
    outlier = np.array([[9.0, 9.0]])
    first = train_isolation_forest(normal)
    second = train_isolation_forest(normal)
    scores = first.score_anomalies(np.vstack([normal, outlier]))
    assert first.n_features == 2
    assert scores == second.score_anomalies(np.vstack([normal, outlier]))
    assert scores[-1] > np.median(scores[:-1])
    assert all(isinstance(score, float) for score in scores)


def test_training_parameters_change_without_affecting_score_contract() -> None:
    data = np.arange(120, dtype=float).reshape(60, 2)
    config = IsolationForestConfig(
        n_estimators=25, max_samples=20, max_features=0.5, bootstrap=True
    )
    trained = train_isolation_forest(data, config)
    assert len(trained.estimator.estimators_) == 25
    assert len(trained.score_anomalies(data[:3])) == 3


@pytest.mark.parametrize(
    "values",
    [
        [],
        [[1, 2], [3, float("nan")]],
        [[1, 2], [3, float("inf")]],
        [[1, "invalid"], [2, 3]],
        [1, 2, 3],
        [[1, 2]],
    ],
)
def test_invalid_training_inputs_fail_before_learning(values: list) -> None:
    with pytest.raises(ValueError):
        train_isolation_forest(values)


def test_inference_requires_identical_feature_dimension() -> None:
    trained = train_isolation_forest([[1, 2], [3, 4], [5, 6]])
    with pytest.raises(ValueError, match="feature count"):
        trained.score_anomalies([[1, 2, 3]])


def test_explicit_sample_and_feature_limits() -> None:
    with pytest.raises(ValueError, match="max_samples"):
        train_isolation_forest([[1], [2]], IsolationForestConfig(max_samples=3))
    with pytest.raises(ValueError, match="max_features"):
        train_isolation_forest([[1], [2]], IsolationForestConfig(max_features=2))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_estimators": 0},
        {"max_samples": 0},
        {"max_samples": 1.5},
        {"max_features": 0.0},
        {"max_features": 2.0},
        {"bootstrap": "yes"},
        {"random_state": -1},
    ],
)
def test_invalid_hyperparameters_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        IsolationForestConfig(**kwargs)
