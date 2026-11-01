"""학습 근거(학습 점수 요약·입력 분포)의 계산. SOS 학습과 외부 생산 Pipeline이 같은 의미로 기록한다.

Model Revision의 `training_score_summary`와 `runtime.feature_training_profile`은 이 함수로만 만든다.
SOS 밖에서 생산한 모델도 같은 함수로 계산해 SOS의 점수 맥락·입력 분포 화면이 같은 뜻을 갖게 한다.
"""

import random

import numpy as np

from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold, score_histogram
from .ml_isolation_forest import TrainedIsolationForest


def training_score_summary(model: TrainedIsolationForest, matrix: np.ndarray) -> tuple[dict, int]:
    """학습 입력 점수의 분포와 상위 5% 임계값. 점수 계산은 배치로 나눈다.

    Args:
        model: 학습한 모델 어댑터.
        matrix: 전처리를 마친 학습 입력(행=관측, 열=Feature). Memmap도 받는다.

    Returns:
        학습 점수 요약과 점수를 계산한 행 수.
    """
    scores = []
    for start in range(0, matrix.shape[0], 8192):
        end = min(start + 8192, matrix.shape[0])
        scores.extend(model.score_anomalies(matrix[start:end]))
    threshold = quantile_threshold(scores, 0.05)
    summary = evaluate_anomaly_scores(scores, threshold=threshold)
    summary["scope"] = "training_data_only"
    summary["histogram"] = score_histogram(scores)
    summary["threshold_source"] = "training_score_quantile_0.95"
    return summary, len(scores)


def feature_training_profile(matrix: np.ndarray, features: list[str]) -> dict:
    """적합 입력의 참고 분포. 행 수와 표본 행렬 크기를 제한한다.

    큰 입력은 고정 시드의 비복원 균등 표본만 읽는다. 학습 입력 자체는 바꾸지 않으며,
    분위수·최솟값·최댓값 모두 분석한 표본의 값임을 기록한다.
    """
    count = matrix.shape[0]
    limit = max(1, min(4096, 262144 // len(features)))
    sampled = count > limit
    if sampled:
        indices = random.Random(42).sample(range(count), limit)
        values = matrix[indices]
    else:
        values = matrix
    return {
        "method": "sampled_linear_quantiles_v1" if sampled else "linear_quantiles_v1",
        "input_space": "model_input_after_preprocessing",
        "row_count": int(count),
        "sample_rows": int(values.shape[0]),
        "sampling_seed": 42 if sampled else None,
        "features": {
            name: dict(zip(("min", "p10", "p50", "p90", "max"),
                           np.quantile(values[:, index], [0, .1, .5, .9, 1],
                                       method="linear").tolist(), strict=True))
            for index, name in enumerate(features)
        },
    }
