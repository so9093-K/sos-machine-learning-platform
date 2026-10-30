"""알고리즘별 출력 계약. 모델 출력의 종류·방향·척도를 정한다.

판본은 학습할 때의 출력 계약을 Runtime 기록(`runtime.output`)에 남긴다. 모델 응답·Scoring·Playground·
온라인 추론·설명은 판본의 기록을 따르므로, 알고리즘의 출력이 바뀌어도 이미 만든 판본의 의미는
바뀌지 않는다. API도 읽으므로 계산 의존성(numpy 등)을 두지 않는다.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class OutputContract:
    output_type: str
    score_direction: str
    score_scale: str

    def record(self) -> dict[str, str]:
        """판본 Runtime 기록에 남기는 형태."""
        return asdict(self)


# 관측마다 이상 점수 하나. 척도는 알고리즘마다 다를 수 있어 점수는 같은 판본끼리 비교한다.
ANOMALY_SCORE_V1 = "anomaly_score_v1"

ISOLATION_FOREST_OUTPUT = OutputContract(
    output_type=ANOMALY_SCORE_V1,
    score_direction="higher_is_more_anomalous",
    score_scale="negative_sklearn_score_samples",
)
# 새로 학습하는 판본이 기록할 출력 계약. 알고리즘의 출력을 바꿀 때는 여기만 바꾼다.
_FOR_NEW_REVISIONS = {"isolation_forest": ISOLATION_FOREST_OUTPUT}
# 출력 계약을 기록하기 전에 학습된 판본이 실제로 낸 출력. 그 판본들의 의미이므로 바꾸지 않는다.
_BEFORE_RECORDING = {
    "isolation_forest": OutputContract(
        output_type="anomaly_score_v1",
        score_direction="higher_is_more_anomalous",
        score_scale="negative_sklearn_score_samples",
    ),
}


def output_contract_for_training(algorithm: str) -> OutputContract | None:
    """새 판본이 학습 때 Runtime 기록에 남길 출력 계약. 모르는 알고리즘이면 None이다."""
    return _FOR_NEW_REVISIONS.get(algorithm)


def revision_output_contract(revision: Mapping) -> OutputContract | None:
    """판본이 학습 때 기록한 출력 계약. 기록 이전 판본은 당시 알고리즘이 낸 출력을 따른다.

    Raises:
        ValueError: 기록된 출력 계약이 손상됐다.
    """
    recorded = (revision.get("runtime") or {}).get("output")
    if recorded is None:
        return _BEFORE_RECORDING.get(revision.get("algorithm"))
    fields = ("output_type", "score_direction", "score_scale")
    if not isinstance(recorded, Mapping) or not all(isinstance(recorded.get(name), str) for name in fields):
        raise ValueError("Model output contract record is invalid")
    return OutputContract(**{name: recorded[name] for name in fields})


def require_output_contract(revision: Mapping) -> OutputContract:
    """실행 경로용. 출력 계약을 정할 수 없는 판본은 계산에 쓰지 않는다.

    Raises:
        ValueError: 지원하지 않는 알고리즘이거나 기록이 손상됐다.
    """
    contract = revision_output_contract(revision)
    if contract is None:
        raise ValueError("Unsupported model algorithm")
    return contract
