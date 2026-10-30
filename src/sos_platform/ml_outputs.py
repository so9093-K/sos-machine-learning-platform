"""알고리즘별 출력 계약. 모델 출력의 종류·방향·척도를 한곳에서 정한다.

Scoring·Playground·온라인 추론·설명이 같은 계약을 쓰고, 소비자와 분석 주제는 output_type으로
출력의 의미를 구별한다. 새 알고리즘은 자기 출력 계약을 여기에 더한다. API도 읽으므로 계산
의존성(numpy 등)을 두지 않는다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OutputContract:
    output_type: str
    score_direction: str
    score_scale: str


# 관측마다 이상 점수 하나. 척도는 알고리즘마다 다를 수 있어 점수는 같은 판본끼리 비교한다.
ANOMALY_SCORE_V1 = "anomaly_score_v1"

ISOLATION_FOREST_OUTPUT = OutputContract(
    output_type=ANOMALY_SCORE_V1,
    score_direction="higher_is_more_anomalous",
    score_scale="negative_sklearn_score_samples",
)
_BY_ALGORITHM = {"isolation_forest": ISOLATION_FOREST_OUTPUT}


def output_contract(algorithm: str) -> OutputContract | None:
    """판본의 알고리즘이 내는 출력 계약. 모르는 알고리즘이면 None이다."""
    return _BY_ALGORITHM.get(algorithm)
