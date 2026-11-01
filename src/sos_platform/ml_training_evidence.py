"""학습 근거(학습 점수 요약·입력 분포)의 계산. SOS 학습과 외부 생산 Pipeline이 같은 의미로 기록한다.

Model Revision의 `training_score_summary`와 `runtime.feature_training_profile`은 이 함수로만 만든다.
SOS 밖에서 생산한 모델도 같은 함수로 계산해 SOS의 점수 맥락·입력 분포 화면이 같은 뜻을 갖게 한다.
"""

import random
from uuid import uuid4

import numpy as np

from .duckdb_spill import SpillBudget
from .ml_isolation_forest import TrainedIsolationForest
from .score_files import (
    ScoreWriter,
    quantile_threshold_file,
    score_histogram_file,
    summarize_scores,
)


def training_score_summary(
    model: TrainedIsolationForest, matrix: np.ndarray, budget: SpillBudget,
) -> tuple[dict, int]:
    """학습 입력 점수의 분포와 상위 5% 임계값.

    점수는 배치로 계산해 Stage의 점수 파일에 쓰고, 요약·임계값·Histogram은 그 파일에서 계산한다.
    학습 행 수와 관계없이 점수를 메모리에 모으지 않으며 의미는 `evaluate_anomaly_scores`와 같다.

    Args:
        model: 학습한 모델 어댑터.
        matrix: 전처리를 마친 학습 입력(행=관측, 열=Feature). Memmap도 받는다.
        budget: 점수 파일과 외부 정렬이 쓰는 Stage·자원 한도.

    Returns:
        학습 점수 요약과 점수를 계산한 행 수.
    """
    path = budget.stage / f"training-scores-{uuid4().hex}.parquet"
    try:
        with ScoreWriter(path) as writer:
            for start in range(0, matrix.shape[0], 8192):
                writer.write(model.score_anomalies(matrix[start:start + 8192]))
        threshold = quantile_threshold_file(path, 0.05, budget)
        summary = summarize_scores(path, threshold=threshold)
        summary["scope"] = "training_data_only"
        summary["histogram"] = score_histogram_file(
            path, low=summary["score_min"], high=summary["score_max"],
            expected_rows=summary["score_count"],
        )
        summary["threshold_source"] = "training_score_quantile_0.95"
    finally:
        path.unlink(missing_ok=True)
    return summary, summary["score_count"]


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
