"""숫자 테이블 입력을 사용하는 첫 Isolation Forest 학습 실행."""

import platform
from dataclasses import asdict
from pathlib import Path

import joblib
import numpy as np
import polars as pl
import sklearn

from .datasets import pinned_scan_dataset
from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from .ml_isolation_forest import IsolationForestConfig, train_isolation_forest

MAX_ROWS = 50_000
MAX_FEATURES = 32
INTEGER_DTYPES = frozenset(
    (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
)
NUMERIC = INTEGER_DTYPES | frozenset((pl.Float32, pl.Float64))


def training_matrix(store, dataset: dict, features: list[str]):
    """학습 입력의 행·특성·dtype를 제한하고 원본의 컬럼 순서를 고정한다."""
    if not 1 <= len(features) <= MAX_FEATURES or len(set(features)) != len(features):
        raise ValueError("Expected 1 to 32 distinct training features")
    with pinned_scan_dataset(store, dataset) as source:
        schema = source.collect_schema()
        if any(field not in schema for field in features):
            raise ValueError("Unknown training feature")
        if any(schema[field] not in NUMERIC for field in features):
            raise ValueError("Only numeric training features are supported")
        frame = source.select(features).head(MAX_ROWS + 1).collect()
    if frame.height > MAX_ROWS or frame.height < 2:
        raise ValueError("Training requires between 2 and 50000 rows")
    if any(frame.null_count().row(0)):
        raise ValueError("Training features contain null values")
    # float64는 2^53을 초과하는 정수를 인접한 값과 구별하지 못한다.
    # 원본 식별 값의 침묵하는 반올림이 학습 피처에 섞이지 않도록 거부한다.
    safe_integer = 2**53
    for field in features:
        if schema[field] in INTEGER_DTYPES:
            values = frame[field]
            if values.max() > safe_integer or values.min() < -safe_integer:
                raise ValueError("Integer feature exceeds the exact float64 range")
    return frame.to_numpy()


def train_dataset(store, dataset: dict, spec: dict, pending: Path) -> dict:
    """자식 Worker가 내부 생성 모델을 임시 파일에 기록한다.

    Args:
        store: Dataset을 고정 조회할 저장소.
        dataset: 버전이 명시된 기존 Dataset.
        spec: 데이터 판본·특성·알고리즘 설정.
        pending: Attempt 전용 로컬 임시 파일.

    Returns:
        dict: 설명적 학습 점수 통계와 모델 재현 조건.
    """
    if dataset["version"] != spec["dataset_version"]:
        raise ValueError("Dataset version mismatch")
    if spec["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model algorithm")
    features = spec["features"]
    config = IsolationForestConfig(**spec["params"])
    matrix = training_matrix(store, dataset, features)
    model = train_isolation_forest(matrix, config)
    scores = model.score_anomalies(matrix)
    threshold = quantile_threshold(scores, 0.05)
    summary = evaluate_anomaly_scores(scores, threshold=threshold)
    summary["scope"] = "training_data_only"
    summary["threshold_source"] = "training_score_quantile_0.95"
    # 안전한 모델 로딩은 현재 제공하지 않는다. 사용자 업로드 파일을 절대 역직렬화하지 않는다.
    joblib.dump(model.estimator, pending)
    return {
        "algorithm": "isolation_forest",
        "dataset_version": dataset["version"],
        "features": features,
        "params": asdict(config),
        "training_rows": len(scores),
        "training_score_summary": summary,
        "runtime": {
            "model_format": "joblib",
            "loading": "trusted_worker_only",
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "polars": pl.__version__,
        },
    }
