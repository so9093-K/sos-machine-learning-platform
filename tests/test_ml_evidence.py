"""연구 결과에서 이상 점수와 감독 평가 지표의 의미를 구분한다."""

from sos_platform.ml_anomaly import (
    evaluate_anomaly_scores,
    normalize_isolation_forest_scores,
    quantile_threshold,
)


def test_unlabeled_anomaly_results_do_not_claim_supervised_accuracy():
    # Isolation Forest의 원시 점수는 낮을수록 이상이다. SOS는 방향을 반전한다.
    scores = normalize_isolation_forest_scores([-0.2, -0.9])
    assert scores == (0.2, 0.9)

    result = evaluate_anomaly_scores(scores, threshold=0.5)
    assert result["score_direction"] == "higher_is_more_anomalous"
    assert result["evaluation_kind"] == "unlabeled"
    assert result["predicted_anomalies"] == 1
    assert result["alert_rate"] == 0.5
    assert not {"precision", "recall", "f1"} & result.keys()


def test_existing_threshold_is_inclusive_even_with_tied_scores():
    """이전 SOS 정책은 임계값과 동일한 점수도 경보에 포함한다."""
    scores = [0.1, 0.5, 0.5, 0.9]
    result = evaluate_anomaly_scores(scores, threshold=0.5, labels=[0, 0, 1, 1])
    assert result["predicted_anomalies"] == 3
    assert result["true_positives"] == 2
    assert result["false_positives"] == 1
    assert result["false_negatives"] == 0


def test_quantile_policy_does_not_guarantee_requested_alarm_fraction_for_ties():
    """동점이 많으면 분위수 목표율과 실제 경보율이 다를 수 있다."""
    scores = [0.4, 0.4, 0.4, 0.4]
    threshold = quantile_threshold(scores, alert_fraction=0.25)
    result = evaluate_anomaly_scores(scores, threshold=threshold)
    assert threshold == 0.4
    assert result["alert_rate"] == 1.0
