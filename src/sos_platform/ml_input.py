"""현재 ML 어댑터들이 공유하는 수치형 입력 타입 계약.

API 사전 검사도 사용하므로 ML 계산 라이브러리에 의존하지 않는다.
"""

import math
from collections.abc import Mapping

import polars as pl

INTEGER_DTYPES = frozenset(
    (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
)
NUMERIC = INTEGER_DTYPES | frozenset((pl.Float32, pl.Float64))
NUMERIC_DTYPE_NAMES = frozenset(str(dtype) for dtype in NUMERIC)


def model_input_issues(
    features: list[str], recorded_dtypes: object, columns: Mapping[str, str]
) -> list[str]:
    """Dataset 컬럼이 모델 입력 Feature 계약과 맞지 않는 이유를 사용자 문장으로 반환한다.

    Feature 이름, 수치형 여부, 모델 Feature 순서별로 기록된 자료형만 비교한다.
    값·결측·범위는 실행 중 검증한다. API 사전 검사와 Worker가 같은 규칙을 사용한다.

    Args:
        features: 모델 Feature 이름(학습 순서).
        recorded_dtypes: 모델 Runtime에 기록된 Feature 자료형 목록.
        columns: Dataset 컬럼 이름 → Polars 자료형 이름.

    Returns:
        list[str]: 문제가 없으면 빈 목록.
    """
    known = (
        isinstance(recorded_dtypes, list)
        and len(recorded_dtypes) == len(features)
        and all(isinstance(dtype, str) for dtype in recorded_dtypes)
    )
    if not known:
        return ["모델의 입력 자료형 기록이 없어 Dataset 호환 여부를 확인할 수 없습니다."]
    missing = [name for name in features if name not in columns]
    mismatched = [
        f"{name}(실제 {columns[name]}, 요구 {expected})"
        for name, expected in zip(features, recorded_dtypes, strict=True)
        if name in columns
        and (columns[name] not in NUMERIC_DTYPE_NAMES or columns[name] != expected)
    ]

    def listed(names: list[str]) -> str:
        return ", ".join(names[:5]) + (f" 외 {len(names) - 5}개" if len(names) > 5 else "")

    issues = []
    if missing:
        issues.append(f"모델 입력 Feature {len(missing)}개가 Dataset에 없습니다: {listed(missing)}")
    if mismatched:
        issues.append(f"자료형이 모델과 다른 Feature {len(mismatched)}개: {listed(mismatched)}")
    return issues

# Job 제출 시 확정하며, 기존 Job에 버전 필드가 없으면 이전 선택 의미를 보존한다.
TRAINING_SELECTION_LEGACY = "row_priority_bottom_k_v1"
TRAINING_SELECTION_CURRENT = "canonical_multiset_v1"


# IEEE 754 binary32의 최대 유한값.
FLOAT32_MAX = 3.4028234663852886e38
MAX_PLAYGROUND_FEATURES = 256


def ordered_playground_input(model: dict, values: dict) -> list[float]:
    """모델 입력 서명과 값의 정확성·결측 조건을 확인해 순서 있는 벡터를 만든다."""
    features = model["features"]
    if not 1 <= len(features) <= MAX_PLAYGROUND_FEATURES or set(values) != set(features):
        raise ValueError("Playground input must exactly match model features")
    allows_missing = model["runtime"].get("artifact_schema") == "if_model_with_preprocessing_v1"
    ordered = []
    expected_dtypes = model["runtime"].get("feature_dtypes")
    if not isinstance(expected_dtypes, list) or len(expected_dtypes) != len(features):
        raise ValueError("Playground Model Feature dtypes are missing")
    for index, name in enumerate(features):
        value = values[name]
        dtype = expected_dtypes[index]
        if dtype not in (
            "Int8", "Int16", "Int32", "Int64",
            "UInt8", "UInt16", "UInt32", "UInt64",
            "Float32", "Float64",
        ):
            raise ValueError("Unsupported Playground Feature dtype")
        if value is None:
            if not allows_missing:
                raise ValueError("This model does not support missing values")
            ordered.append(float("nan"))
        elif isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError("Playground Features must be numeric or explicit missing")
        elif isinstance(value, int) and abs(value) > 2**53:
            raise ValueError("Playground integer exceeds exact float64 range")
        elif not math.isfinite(value):
            raise ValueError("Non-finite Playground value")
        elif dtype.startswith(("Int", "UInt")) and not float(value).is_integer():
            raise ValueError("Integer Playground Feature must be an integer")
        elif dtype.startswith(("Int", "UInt")):
            bits = int(dtype[4:] if dtype.startswith("UInt") else dtype[3:])
            minimum = 0 if dtype.startswith("UInt") else -(2 ** (bits - 1))
            maximum = 2**bits - 1 if dtype.startswith("UInt") else 2 ** (bits - 1) - 1
            if not minimum <= value <= maximum:
                raise ValueError("Integer Playground Feature is outside its dtype range")
            ordered.append(float(value))
        elif dtype == "Float32" and abs(value) > FLOAT32_MAX:
            raise ValueError("Float32 Playground Feature is outside its dtype range")
        else:
            ordered.append(float(value))
    return ordered
