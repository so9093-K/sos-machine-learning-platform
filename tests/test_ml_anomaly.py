"""이상 점수·정책·평가의 불변 의미 검증."""

import math

import pytest

from sos_platform.ml_anomaly import (
    evaluate_anomaly_scores,
    normalize_isolation_forest_scores,
    quantile_threshold,
)


def test_sklearn_score_direction_is_explicit() -> None:
    assert normalize_isolation_forest_scores([-0.2, -0.9]) == (0.2, 0.9)


def test_quantile_threshold_uses_only_reference_scores() -> None:
    assert quantile_threshold([1, 2, 3, 4, 5], 0.2) == pytest.approx(4.2)


def test_tied_scores_do_not_promise_exact_alarm_fraction() -> None:
    boundary = quantile_threshold([0.7, 0.7, 0.7, 0.7], 0.25)
    result = evaluate_anomaly_scores([0.7] * 4, threshold=boundary)
    assert result["alert_rate"] == 1.0


def test_unlabeled_does_not_invent_detection_accuracy() -> None:
    outcome = evaluate_anomaly_scores([0.1, 0.9], threshold=0.5)
    assert outcome["evaluation_kind"] == "unlabeled"
    assert outcome["predicted_anomalies"] == 1
    assert "precision" not in outcome
    assert "recall" not in outcome


def test_scoring_without_policy_is_valid() -> None:
    result = evaluate_anomaly_scores([0.1, 0.2])
    assert result["threshold"] is None
    assert result["alert_rate"] is None


def test_labeled_metrics_are_computed_from_explicit_ground_truth() -> None:
    result = evaluate_anomaly_scores([0.1, 0.7, 0.9, 0.4], threshold=0.5, labels=[0, 0, 1, 1])
    assert (result["true_positives"], result["false_positives"]) == (1, 1)
    assert (result["true_negatives"], result["false_negatives"]) == (1, 1)
    assert result["precision"] == result["recall"] == result["f1"] == 0.5


def test_missing_positive_class_does_not_fabricate_recall() -> None:
    result = evaluate_anomaly_scores([0.1, 0.2], threshold=0.3, labels=[0, 0])
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["f1"] is None


@pytest.mark.parametrize(
    "scores",
    [[], [float("nan")], [float("inf")], [True], ["0.1"]],
)
def test_invalid_scores_rejected(scores: list) -> None:
    with pytest.raises(ValueError):
        evaluate_anomaly_scores(scores)


@pytest.mark.parametrize("fraction", [0, 1, -0.1, float("nan"), float("inf"), True])
def test_invalid_quantiles_rejected(fraction: float) -> None:
    with pytest.raises(ValueError):
        quantile_threshold([0.1, 0.2], fraction)


def test_labels_require_threshold_and_same_length() -> None:
    with pytest.raises(ValueError, match="threshold"):
        evaluate_anomaly_scores([0.4], labels=[1])
    with pytest.raises(ValueError, match="Labels"):
        evaluate_anomaly_scores([0.4], threshold=0.3, labels=[])
    with pytest.raises(ValueError, match="binary"):
        evaluate_anomaly_scores([0.4], threshold=0.3, labels=[2])


def test_invalid_threshold_is_rejected() -> None:
    for value in (math.nan, math.inf, True, "0.4"):
        with pytest.raises(ValueError):
            evaluate_anomaly_scores([0.4], threshold=value)
