"""이상 탐지 점수의 방향·임계값·평가 의미를 정의하는 순수 로직.

프레임워크의 점수 공간을 플랫폼의 의사결정과 혼동하지 않는다.
학습 모델·저장소·HTTP·MLflow에 의존하지 않는 계약만 포함한다.
"""

import math
from bisect import bisect_right
from collections.abc import Sequence

from .ml_outputs import ISOLATION_FOREST_OUTPUT


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


def histogram_edges(low: float, high: float, *, bins: int = 32) -> list[float]:
    """실제 점수 범위의 고정 폭 구간을 결정한다. 동일 점수일 때는 표시 폭만 부여한다."""
    if not isinstance(bins, int) or isinstance(bins, bool) or not 1 <= bins <= 128:
        raise ValueError("Histogram bin count is invalid")
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise ValueError("Histogram score bounds are invalid")
    if low == high:
        half_width = max(abs(low) * 0.01, 1e-6)
        left, right = low - half_width, high + half_width
    else:
        left, right = low, high
    step = (right - left) / bins
    if not math.isfinite(step) or step <= 0:
        raise ValueError("Histogram score range is invalid")
    edges = [left + step * i for i in range(bins)] + [right]
    if any(previous >= following for previous, following in zip(edges, edges[1:])):
        # 근접한 float 점수 두 값의 차이가 32개 구간보다 작으면 엣지가
        # 같은 float로 반올림된다. 구간만 확장해 중복 경계를 없앤다.
        min_span = 2 * bins * max(math.ulp(low), math.ulp(high))
        expanded = (min_span - (high - low)) / 2
        left, right = low - expanded, high + expanded
        step = (right - left) / bins
        edges = [left + step * i for i in range(bins)] + [right]
        if not all(math.isfinite(edge) for edge in edges) or any(
            previous >= following for previous, following in zip(edges, edges[1:])
        ):
            raise ValueError("Histogram score range cannot be represented accurately")
    return edges


def score_histogram(values: Sequence[float], *, bins: int = 32) -> dict:
    """Train 점수로 정확한 구간 빈도를 계산하되 원래 점수와 판정 의미는 변경하지 않는다."""
    checked = _finite_scores(values)
    low, high = min(checked), max(checked)
    edges = histogram_edges(low, high, bins=bins)
    counts = [0] * bins
    for score in checked:
        slot = min(max(bisect_right(edges, score) - 1, 0), bins - 1)
        counts[slot] += 1
    return {
        "method": "equal_width_exact_v1",
        "bin_edges": edges,
        "bin_counts": counts,
        "score_count": len(checked),
        "constant_scores": low == high,
    }


def checked_alert_fraction(alert_fraction: object) -> float:
    if isinstance(alert_fraction, bool) or not isinstance(alert_fraction, (int, float)):
        raise ValueError("Alert fraction must be numeric")
    if not math.isfinite(alert_fraction) or not 0 < alert_fraction < 1:
        raise ValueError("Alert fraction must be between 0 and 1")
    return float(alert_fraction)


def quantile_position(count: int, alert_fraction: float) -> tuple[int, int, float]:
    """오름차순 점수에서 임계값을 보간할 두 순위와 보간 위치. 파일·메모리 계산이 같이 쓴다."""
    position = (count - 1) * (1 - checked_alert_fraction(alert_fraction))
    return math.floor(position), math.ceil(position), position


def interpolate_threshold(lower_value: float, upper_value: float, lower: int, position: float) -> float:
    return lower_value + (upper_value - lower_value) * (position - lower)


def quantile_threshold(reference_scores: Sequence[float], alert_fraction: float) -> float:
    """독립 참조 집합의 상위 점수 비율에 대응하는 보간 임계값을 산출한다.

    동점과 보간 때문에 실제 판정 비율이 alert_fraction과 같다는 보장은 없다.
    운영 시 참조 집합의 판본·선정 기준을 정책과 함께 보존해야 한다.
    """
    checked_alert_fraction(alert_fraction)
    ordered = sorted(_finite_scores(reference_scores))
    lower, upper, position = quantile_position(len(ordered), alert_fraction)
    return interpolate_threshold(ordered[lower], ordered[upper], lower, position)


def checked_threshold(threshold: object) -> float | None:
    if threshold is None:
        return None
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("Threshold must be numeric")
    if not math.isfinite(threshold):
        raise ValueError("Threshold must be finite")
    return float(threshold)


def anomaly_score_summary(
    *,
    count: int,
    score_min: float,
    score_max: float,
    score_mean: float,
    threshold: float | None = None,
    predicted: int | None = None,
    confusion: tuple[int, int, int, int] | None = None,
) -> dict:
    """점수 집계에서 판정·평가 결과를 만든다. 메모리 내 점수와 파일 순회가 같은 의미를 쓴다.

    Args:
        count: 점수 수.
        score_min / score_max / score_mean: 점수 분포. 평균은 정확 합(fsum)으로 계산한다.
        threshold: 판정 임계값(점수 >= 임계값이면 이상). 없으면 판정하지 않는다.
        predicted: 임계값 이상인 점수 수.
        confusion: 레이블이 있을 때 (tp, fp, tn, fn).
    """
    result: dict = {
        "score_direction": ISOLATION_FOREST_OUTPUT.score_direction,
        "score_count": count,
        "score_min": score_min,
        "score_max": score_max,
        "score_mean": score_mean,
        "threshold": None,
        "predicted_anomalies": None,
        "alert_rate": None,
        "evaluation_kind": "unlabeled",
    }
    if threshold is not None:
        result.update(
            threshold=float(threshold),
            predicted_anomalies=predicted,
            alert_rate=predicted / count,
        )
    if confusion is None:
        return result
    tp, fp, tn, fn = confusion
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
    threshold = checked_threshold(threshold)
    predicted = (
        sum(score >= threshold for score in checked) if threshold is not None else None
    )
    confusion = None
    if labels is not None:
        if threshold is None:
            raise ValueError("Labeled decision evaluation requires a threshold")
        if len(labels) != len(checked) or any(label not in (0, 1) for label in labels):
            raise ValueError("Labels must be binary and match the score count")
        pairs = tuple(zip(checked, labels, strict=True))
        tp = sum(score >= threshold and label == 1 for score, label in pairs)
        fp = sum(score >= threshold and label == 0 for score, label in pairs)
        fn = sum(score < threshold and label == 1 for score, label in pairs)
        confusion = (tp, fp, len(checked) - tp - fp - fn, fn)
    return anomaly_score_summary(
        count=len(checked), score_min=min(checked), score_max=max(checked),
        score_mean=math.fsum(checked) / len(checked),
        threshold=threshold, predicted=predicted, confusion=confusion,
    )
