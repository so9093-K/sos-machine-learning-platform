"""알고리즘별 출력 계약. 모델 출력의 종류·방향·척도를 정한다.

판본은 학습할 때의 출력 계약을 Runtime 기록(`runtime.output`)에 남긴다. 모델 응답·Scoring·Playground·
온라인 추론·설명은 판본의 기록을 따르므로, 알고리즘의 출력이 바뀌어도 이미 만든 판본의 의미는
바뀌지 않는다. API도 읽으므로 계산 의존성(numpy 등)을 두지 않는다.
"""

import math
from collections.abc import Mapping, Sequence
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
# 출력 종류마다 관측 하나의 결과 구조. 출력 종류를 더할 때는 그 종류를 만드는 계산과 이 구조·검증을
# 함께 더한다. 메타데이터만 바꿔 다른 종류를 기존 구조로 내보내지 않게 한다.
_RESULT_FIELDS = {ANOMALY_SCORE_V1: "anomaly_score"}

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


def require_output_contract(revision: Mapping, *, produces: str) -> OutputContract:
    """실행 경로용. 판본의 출력 종류가 이 경로가 실제로 계산하는 종류일 때만 계산에 쓴다.

    Args:
        revision: 계산할 Model Revision.
        produces: 이 경로가 만드는 출력 종류(예: 이상 점수 경로는 ANOMALY_SCORE_V1).

    Raises:
        ValueError: 지원하지 않는 알고리즘, 손상된 기록 또는 이 경로가 만들지 않는 출력 종류.
    """
    contract = revision_output_contract(revision)
    if contract is None:
        raise ValueError("Unsupported model algorithm")
    if contract.output_type != produces:
        raise ValueError(f"Model output type {contract.output_type} is not produced by this path")
    return contract


def output_records(contract: OutputContract, values: Sequence[float]) -> list[dict[str, float]]:
    """계산 결과를 출력 종류의 결과 구조로 만든다. 관측 하나에 결과 하나다."""
    field = _RESULT_FIELDS[contract.output_type]
    return [{field: float(value)} for value in values]


def _score(value: object) -> bool:
    """유한한 실수 점수. bool은 int의 하위 유형이지만 점수가 아니다."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def response_matches(contract: OutputContract, response: object, count: int) -> bool:
    """응답이 판본의 출력 계약(종류·점수 방향·척도)과 그 종류의 결과 구조를 모두 따른다.

    필드가 빠지거나 다르면 거짓이다. 관측별 결과는 입력 관측 수와 같아야 한다.
    """
    if not isinstance(response, Mapping) or any(
            response.get(name) != value for name, value in contract.record().items()):
        return False
    field = _RESULT_FIELDS.get(contract.output_type)
    records = response.get("outputs")
    return field is not None and isinstance(records, list) and len(records) == count and all(
        isinstance(record, dict) and set(record) == {field} and _score(record[field])
        for record in records)
