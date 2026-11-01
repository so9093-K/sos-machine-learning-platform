"""SOS가 연결하는 생산 모델 패키지 계약. 생산 Pipeline과 SOS 연결이 같은 규칙을 쓴다.

모델 생산(학습·평가·MLflow 등록)은 SOS 밖의 Pipeline이 맡는다. 생산 측은 MLflow 모델 버전의
아티팩트 위치(`source`)에 다음 두 파일을 둔다.

- `estimator.skops`: 허용한 타입만 복원되는 scikit-learn IsolationForest.
- `sos_model.json`: 입력 서명, 학습 중앙값 전처리, 매개변수, 학습 점수 근거, 생산 Runtime, 검증 표본.

SOS는 패키지의 코드를 실행하지 않는다. 전처리는 기록한 중앙값으로 다시 구성하고, 검증 표본의 점수를
SOS Runtime에서 다시 계산해 생산 측 점수와 같은지 확인한 뒤에만 Model Revision으로 게시한다.
"""

import io
import json
import math
import platform
from dataclasses import asdict, fields
from pathlib import Path

import numpy as np
import sklearn

from .ml_if_config import IsolationForestConfig
from .ml_if_preprocessing import FittedIFMedianImputer
from .ml_input import NUMERIC_DTYPE_NAMES
from .ml_isolation_forest import TrainedIsolationForest
from .ml_outputs import ISOLATION_FOREST_OUTPUT
from .ml_runtime import PREPROCESSING_MODE, load_skops_estimator

MANIFEST_FILE = "sos_model.json"
ESTIMATOR_FILE = "estimator.skops"
MANIFEST_SCHEMA = "sos_model_v1"
MAX_MANIFEST_BYTES = 1_048_576
MAX_ESTIMATOR_BYTES = 256 * 1024 * 1024
MAX_FEATURES = 256
_QUANTILES = ("min", "p10", "p50", "p90", "max")
_PROFILE_METHODS = ("linear_quantiles_v1", "sampled_linear_quantiles_v1")
_SUMMARY_KEYS = {
    "score_direction", "score_count", "score_min", "score_max", "score_mean", "threshold",
    "predicted_anomalies", "alert_rate", "evaluation_kind", "scope", "histogram",
    "threshold_source",
}
_HISTOGRAM_KEYS = {"method", "bin_edges", "bin_counts", "score_count", "constant_scores"}
_TRAINING_KEYS = {"producer", "data_reference", "data_sha256", "rows", "evaluation"}


def _number(value: object) -> bool:
    # JSON 정수는 크기 제한이 없으므로 float 변환 전에 범위를 본다.
    if type(value) is int:
        return abs(value) <= 2**53
    return type(value) is float and math.isfinite(value)


def _count(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**53


def _validate_features(manifest: dict) -> list[str]:
    features = manifest.get("features")
    if (
        not isinstance(features, list) or not 1 <= len(features) <= MAX_FEATURES
        or any(not isinstance(name, str) or not name or len(name) > 256 for name in features)
        or len(set(features)) != len(features)
    ):
        raise ValueError("Model package features are invalid")
    dtypes = manifest.get("feature_dtypes")
    if (
        not isinstance(dtypes, list) or len(dtypes) != len(features)
        or any(dtype not in NUMERIC_DTYPE_NAMES for dtype in dtypes)
    ):
        raise ValueError("Model package feature dtypes are invalid")
    return features


def _validate_score_summary(summary: object) -> None:
    """생산 측이 SOS의 `training_score_summary`로 계산한 구조인지 확인한다."""
    if not isinstance(summary, dict) or set(summary) != _SUMMARY_KEYS:
        raise ValueError("Model package training score summary is invalid")
    count = summary["score_count"]
    low, high, mean, threshold = (
        summary["score_min"], summary["score_max"], summary["score_mean"], summary["threshold"],
    )
    if (
        summary["score_direction"] != ISOLATION_FOREST_OUTPUT.score_direction
        or summary["evaluation_kind"] != "unlabeled"
        or summary["scope"] != "training_data_only"
        or summary["threshold_source"] != "training_score_quantile_0.95"
        or not _count(count, minimum=2)
        or not all(_number(value) for value in (low, high, mean, threshold))
        or not low <= threshold <= high
        or not _count(summary["predicted_anomalies"])
        or summary["predicted_anomalies"] > count
        or not _number(summary["alert_rate"])
        or not math.isclose(summary["alert_rate"], summary["predicted_anomalies"] / count)
    ):
        raise ValueError("Model package training score summary is invalid")
    histogram = summary["histogram"]
    if not isinstance(histogram, dict) or set(histogram) != _HISTOGRAM_KEYS:
        raise ValueError("Model package score histogram is invalid")
    edges, counts = histogram["bin_edges"], histogram["bin_counts"]
    if (
        histogram["method"] != "equal_width_exact_v1"
        or histogram["score_count"] != count
        or type(histogram["constant_scores"]) is not bool
        or not isinstance(counts, list) or not 1 <= len(counts) <= 128
        or any(not _count(value) for value in counts)
        or sum(counts) != count
        or not isinstance(edges, list) or len(edges) != len(counts) + 1
        or any(not _number(edge) for edge in edges)
        or any(left >= right for left, right in zip(edges, edges[1:], strict=False))
    ):
        raise ValueError("Model package score histogram is invalid")


def _validate_profile(profile: object, features: list[str]) -> None:
    if not isinstance(profile, dict) or set(profile) != {
        "method", "input_space", "row_count", "sample_rows", "sampling_seed", "features",
    }:
        raise ValueError("Model package feature profile is invalid")
    ranges = profile["features"]
    if (
        profile["method"] not in _PROFILE_METHODS
        or profile["input_space"] != "model_input_after_preprocessing"
        or not _count(profile["row_count"], minimum=2)
        or not _count(profile["sample_rows"], minimum=1)
        or profile["sample_rows"] > profile["row_count"]
        or not (profile["sampling_seed"] is None or _count(profile["sampling_seed"]))
        or not isinstance(ranges, dict) or set(ranges) != set(features)
    ):
        raise ValueError("Model package feature profile is invalid")
    for name in features:
        values = ranges[name]
        if not isinstance(values, dict) or set(values) != set(_QUANTILES):
            raise ValueError("Model package feature profile is invalid")
        ordered = [values[key] for key in _QUANTILES]
        if not all(_number(value) for value in ordered) or ordered != sorted(ordered):
            raise ValueError("Model package feature profile is invalid")


def _validate_training(training: object) -> None:
    """생산 측의 학습 근거. SOS는 보관·표시만 하며 값으로 실행을 바꾸지 않는다."""
    if not isinstance(training, dict) or not set(training) <= _TRAINING_KEYS:
        raise ValueError("Model package training evidence is invalid")
    producer = training.get("producer")
    if not isinstance(producer, str) or not 1 <= len(producer) <= 200:
        raise ValueError("Model package producer is invalid")
    reference = training.get("data_reference")
    if reference is not None and (not isinstance(reference, str) or len(reference) > 1000):
        raise ValueError("Model package data reference is invalid")
    digest = training.get("data_sha256")
    if digest is not None and (
        not isinstance(digest, str) or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("Model package data digest is invalid")
    rows = training.get("rows")
    if rows is not None and (
        not isinstance(rows, dict) or not set(rows) <= {"train", "calibration", "test"}
        or any(not _count(value) for value in rows.values())
    ):
        raise ValueError("Model package row counts are invalid")
    evaluation = training.get("evaluation")
    if evaluation is not None and (
        not isinstance(evaluation, dict) or len(json.dumps(evaluation)) > 65_536
    ):
        raise ValueError("Model package evaluation is invalid")


def _validate_verification(verification: object, n_features: int) -> None:
    if not isinstance(verification, dict) or set(verification) != {"inputs", "scores"}:
        raise ValueError("Model package verification sample is invalid")
    inputs, scores = verification["inputs"], verification["scores"]
    if (
        not isinstance(inputs, list) or not 1 <= len(inputs) <= 16
        or not isinstance(scores, list) or len(scores) != len(inputs)
        or any(not _number(score) for score in scores)
        or any(
            not isinstance(row, list) or len(row) != n_features
            or any(value is not None and not _number(value) for value in row)
            for row in inputs
        )
    ):
        raise ValueError("Model package verification sample is invalid")


def read_manifest(raw: bytes) -> dict:
    """`sos_model.json`을 읽고 SOS가 게시할 수 있는 구조인지 확인한다.

    Raises:
        ValueError: 크기·JSON·필드 구조·값 범위가 계약과 다르다.
    """
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Model package manifest is too large")

    def reject_constant(name: str) -> None:
        raise ValueError(f"Model package manifest contains {name}")

    try:
        manifest = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Model package manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema", "algorithm", "features", "feature_dtypes", "preprocessing", "params",
        "runtime", "training_score_summary", "feature_training_profile", "training",
        "verification",
    }:
        raise ValueError("Model package manifest fields are invalid")
    if manifest["schema"] != MANIFEST_SCHEMA or manifest["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model package")
    features = _validate_features(manifest)
    preprocessing = manifest["preprocessing"]
    if (
        not isinstance(preprocessing, dict) or set(preprocessing) != {"mode", "medians"}
        or preprocessing["mode"] != PREPROCESSING_MODE
        or not isinstance(preprocessing["medians"], list)
        or len(preprocessing["medians"]) != len(features)
        or any(not _number(value) for value in preprocessing["medians"])
    ):
        raise ValueError("Model package preprocessing is invalid")
    params = manifest["params"]
    if not isinstance(params, dict) or set(params) != {item.name for item in fields(IsolationForestConfig)}:
        raise ValueError("Model package parameters are invalid")
    IsolationForestConfig(**params)
    runtime = manifest["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {"python", "numpy", "scikit_learn", "skops"} or any(
        not isinstance(value, str) or not value or len(value) > 64 for value in runtime.values()
    ):
        raise ValueError("Model package runtime is invalid")
    _validate_score_summary(manifest["training_score_summary"])
    _validate_profile(manifest["feature_training_profile"], features)
    _validate_training(manifest["training"])
    _validate_verification(manifest["verification"], len(features))
    return manifest


def _matrix(rows: list[list[float | None]]) -> np.ndarray:
    return np.asarray([[math.nan if value is None else value for value in row] for row in rows],
                      dtype=np.float64)


def package_isolation_forest(
    model: TrainedIsolationForest,
    *,
    feature_dtypes: list[str],
    training_score_summary: dict,
    feature_training_profile: dict,
    training: dict,
) -> dict[str, bytes]:
    """학습한 모델을 SOS가 연결할 수 있는 패키지 파일로 만든다.

    검증 표본은 학습 분포의 분위수 행과 전체 결측 행(중앙값 대체)이다. 원본 학습 행은 넣지 않는다.

    Args:
        model: 학습 중앙값 전처리를 가진 모델 어댑터.
        feature_dtypes: Feature 순서별 Polars 자료형 이름.
        training_score_summary: `training_score_summary`의 결과.
        feature_training_profile: 전처리 후 학습 입력의 분위수 요약.
        training: 생산 측 학습 근거(producer·data_reference·data_sha256·rows·evaluation).

    Returns:
        파일 이름 → 바이트. MLflow 모델 버전의 아티팩트 위치에 그대로 올린다.

    Raises:
        ValueError: 패키지가 SOS 연결 계약을 만족하지 않는다.
    """
    import skops
    import skops.io as sio

    preprocessing = model.preprocessing
    if preprocessing is None:
        raise ValueError("Model package requires train-median preprocessing")
    features = list(preprocessing.feature_names)
    ranges = feature_training_profile.get("features") or {}
    inputs = [[ranges[name][key] for name in features] for key in _QUANTILES]
    inputs.append([None] * len(features))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "algorithm": "isolation_forest",
        "features": features,
        "feature_dtypes": list(feature_dtypes),
        "preprocessing": {"mode": PREPROCESSING_MODE, "medians": list(preprocessing.medians)},
        "params": asdict(model.config),
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "skops": skops.__version__,
        },
        "training_score_summary": training_score_summary,
        "feature_training_profile": feature_training_profile,
        "training": training,
        "verification": {
            "inputs": inputs,
            "scores": list(model.score_anomalies(_matrix(inputs))),
        },
    }
    raw = json.dumps(manifest, allow_nan=False, ensure_ascii=False).encode("utf-8")
    read_manifest(raw)
    buffer = io.BytesIO(sio.dumps(model.estimator))
    if buffer.getbuffer().nbytes > MAX_ESTIMATOR_BYTES:
        raise ValueError("Model package estimator is too large")
    return {MANIFEST_FILE: raw, ESTIMATOR_FILE: buffer.getvalue()}


def restore_package(manifest: dict, estimator_path: Path) -> TrainedIsolationForest:
    """허용 타입만 복원하고, 기록한 서명·매개변수·검증 점수가 SOS에서 재현되는지 확인한다.

    Args:
        manifest: `read_manifest`로 확인한 패키지 기록.
        estimator_path: 내려받은 `estimator.skops`.

    Returns:
        SOS Runtime에서 복원한 모델 어댑터.

    Raises:
        ValueError: 허용하지 않은 타입, 서명·매개변수 불일치, 검증 점수 불일치.
    """
    if estimator_path.stat().st_size > MAX_ESTIMATOR_BYTES:
        raise ValueError("Model package estimator is too large")
    estimator = load_skops_estimator(estimator_path)
    features = manifest["features"]
    config = IsolationForestConfig(**manifest["params"])
    if estimator.n_features_in_ != len(features):
        raise ValueError("Model package estimator does not match its feature signature")
    restored = estimator.get_params()
    if any(restored[key] != value for key, value in asdict(config).items()):
        raise ValueError("Model package estimator does not match its parameters")
    preprocessing = FittedIFMedianImputer(
        tuple(features), tuple(manifest["preprocessing"]["medians"])
    )
    model = TrainedIsolationForest(estimator, config, len(features), preprocessing=preprocessing)
    verification = manifest["verification"]
    scores = model.score_anomalies(_matrix(verification["inputs"]))
    if any(
        not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
        for actual, expected in zip(scores, verification["scores"], strict=True)
    ):
        raise ValueError("Model package scores do not reproduce in the SOS runtime")
    return model
