"""소량 관측치를 신뢰된 Worker 자식 프로세스에서 점수화한다."""

import math
import numpy as np

from .ml_runtime import load_trusted_isolation_forest

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
        elif dtype == "Float32" and abs(value) > float(np.finfo(np.float32).max):
            raise ValueError("Float32 Playground Feature is outside its dtype range")
        else:
            ordered.append(float(value))
    return ordered


def run_playground_scoring(store, spec: dict) -> dict:
    """동일 모델 어댑터로 실제 점수를 산출한다. Dataset·Scoring Result를 만들지 않는다."""
    model = store.get_model_revision(spec["model_revision_id"])
    if not model or model["owner_actor"] != spec["model_owner_actor"]:
        raise ValueError("Playground model is unavailable or ownership changed")
    values = ordered_playground_input(model, spec["features"])
    adapter = load_trusted_isolation_forest(model, store.home / "cache")
    score = adapter.score_anomalies(np.array([values], dtype=np.float64))[0]
    if not math.isfinite(score):
        raise ValueError("Playground score is not finite")
    return {
        "model_revision_id": model["id"],
        "anomaly_score": float(score),
        "score_direction": "higher_is_more_anomalous",
        "score_scale": "negative_sklearn_score_samples",
        "feature_count": len(values),
    }
