"""연구 결과에서 이상 점수와 감독 평가 지표의 의미를 구분한다."""

from sos_platform.ml_anomaly import (
    evaluate_anomaly_scores,
    normalize_isolation_forest_scores,
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
