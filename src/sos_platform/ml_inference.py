"""신뢰된 모델 어댑터로 관측값 점수를 계산한다.

기록형 Playground Job과 온라인 추론(Ray Serve)이 같은 계산을 쓴다. 출력의 의미는 ml_outputs,
모델 복원·검증은 ml_runtime, 입력 서명 검증과 정렬은 ml_input이 맡는다.
"""

import math
from collections.abc import Sequence

import numpy as np


def score_observations(adapter, rows: Sequence[Sequence[float | None]]) -> list[float]:
    """정렬된 입력 행의 이상 점수를 계산한다. None은 결측으로 학습 기준 전처리를 따른다.

    Raises:
        ValueError: 계산된 점수가 유한하지 않은 경우.
    """
    matrix = np.array([[math.nan if value is None else value for value in row] for row in rows],
                      dtype=np.float64)
    scores = [float(score) for score in adapter.score_anomalies(matrix)]
    if not all(math.isfinite(score) for score in scores):
        raise ValueError("Anomaly score is not finite")
    return scores
