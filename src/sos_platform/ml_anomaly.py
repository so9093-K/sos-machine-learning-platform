"""이상 탐지 점수의 방향·임계값·평가 의미를 정의하는 순수 로직.

프레임워크의 점수 공간을 플랫폼의 의사결정과 혼동하지 않는다.
학습 모델·저장소·HTTP·MLflow에 의존하지 않는 계약만 포함한다.
"""

import math
from collections.abc import Sequence


def _finite_scores(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        raise ValueError("Anomaly scores must not be empty")
    checked = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Anomaly scores must be numeric")
        if not math.isfinite(value):
            raise ValueError("Anomaly scores must be finite")
        checked.append(float(value))
    return tuple(checked)


def normalize_isolation_forest_scores(raw_scores: Sequence[float]) -> tuple[float, ...]:
    """scikit-learn score_samples를 높을수록 이상인 점수로 변환한다.

    점수는 확률이 아니며 다른 모델·판본 간 직접 비교를 보장하지 않는다.
    """
    return tuple(-value for value in _finite_scores(raw_scores))


def quantile_threshold(reference_scores: Sequence[float], alert_fraction: float) -> float:
    """독립 참조 집합의 상위 점수 비율에 대응하는 보간 임계값을 산출한다.

    동점과 보간 때문에 실제 판정 비율이 alert_fraction과 같다는 보장은 없다.
    운영 시 참조 집합의 판본·선정 기준을 정책과 함께 보존해야 한다.
    """
    if isinstance(alert_fraction, bool) or not isinstance(alert_fraction, (int, float)):
        raise ValueError("Alert fraction must be numeric")
    if not math.isfinite(alert_fraction) or not 0 < alert_fraction < 1:
        raise ValueError("Alert fraction must be between 0 and 1")
    ordered = sorted(_finite_scores(reference_scores))
    position = (len(ordered) - 1) * (1 - alert_fraction)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate_anomaly_scores(
    scores: Sequence[float],
    *,
    threshold: float | None = None,
    labels: Sequence[int] | None = None,
) -> dict:
    """높을수록 이상인 점수의 판정 결과와 가능한 범위의 근거만 반환한다.

    레이블이 없는 결과에 Precision·Recall 등 감독 지표를 만들지 않는다.
    레이블이 있는 경우에도 양성 또는 예측 양성이 없으면 미정의 지표는 None이다.
    """
    checked = _finite_scores(scores)
    result: dict = {
        "score_direction": "higher_is_more_anomalous",
        "score_count": len(checked),
        "score_min": min(checked),
        "score_max": max(checked),
        "score_mean": math.fsum(checked) / len(checked),
        "threshold": None,
        "predicted_anomalies": None,
        "alert_rate": None,
        "evaluation_kind": "unlabeled",
    }
    if threshold is not None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("Threshold must be numeric")
        if not math.isfinite(threshold):
            raise ValueError("Threshold must be finite")
        predicted = tuple(score >= threshold for score in checked)
        alarms = sum(predicted)
        result.update(
            threshold=float(threshold),
            predicted_anomalies=alarms,
            alert_rate=alarms / len(checked),
        )
    if labels is None:
        return result
    if threshold is None:
        raise ValueError("Labeled decision evaluation requires a threshold")
    if len(labels) != len(checked) or any(label not in (0, 1) for label in labels):
        raise ValueError("Labels must be binary and match the score count")
    tp = sum(
        score >= threshold and label == 1 for score, label in zip(checked, labels, strict=True)
    )
    fp = sum(
        score >= threshold and label == 0 for score, label in zip(checked, labels, strict=True)
    )
    fn = sum(score < threshold and label == 1 for score, label in zip(checked, labels, strict=True))
    tn = len(checked) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    result.update(
        evaluation_kind="labeled",
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1=f1,
    )
    return result
