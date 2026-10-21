"""Isolation Forest의 명시된 학습 입력을 실행 예산 안에서 재현 가능하게 선택한다."""

import hashlib
import platform
from dataclasses import asdict
from pathlib import Path

import joblib
import numpy as np
import polars as pl
import sklearn

from .datasets import pinned_scan_dataset
from .errors import PlatformError
from .execution_budget import ExecutionBudget
from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from .ml_input import (
    INTEGER_DTYPES,
    NUMERIC,
    TRAINING_SELECTION_CURRENT,
    TRAINING_SELECTION_LEGACY,
)
from .ml_isolation_forest import IsolationForestConfig, train_isolation_forest
from .ml_sampling import canonical_sorted_batches
from .schemas import MLTrainingInputPolicy


def training_matrix(
    store,
    dataset: dict,
    features: list[str],
    *,
    input_policy: MLTrainingInputPolicy,
    worker_memory_mib: int | None = None,
    selection_algorithm_version: str = TRAINING_SELECTION_CURRENT,
) -> tuple[np.ndarray, dict]:
    """명시된 입력 정책을 Worker 예산에서 처리한다.

    Args:
        store: 원본 Dataset 저장소.
        dataset: 고정 판본 Dataset 참조.
        features: 순서가 확정된 숫자형 Feature 목록.
        input_policy: 전체 사용 또는 행 수·Seed가 지정된 부분집합.
        worker_memory_mib: Worker RSS 예산. 없으면 실행 환경에서 읽는다.
        selection_algorithm_version: Job 제출 시 확정된 표본 선택 알고리즘 판본.

    Returns:
        정렬된 float64 학습 행렬과 실제 입력 선택 근거.

    Raises:
        ValueError: Dataset 값·행 수나 입력 선택 조건이 올바르지 않을 때.
        PlatformError: 요청한 학습 규모를 Worker 자원에서 수용할 수 없을 때.
    """
    if not features or len(set(features)) != len(features):
        raise ValueError("Training features must be nonempty and distinct")
    if selection_algorithm_version not in (TRAINING_SELECTION_CURRENT, TRAINING_SELECTION_LEGACY):
        raise ValueError("Unsupported training input selection algorithm")
    budget_mib = (
        ExecutionBudget.from_env().max_rss_mib
        if worker_memory_mib is None
        else worker_memory_mib
    )
    if budget_mib <= 0:
        raise ValueError("Training memory budget must be positive")
    # 행렬·점수·모델·Reader 객체를 함께 고려하므로 원본 행렬에는 일부만 할당.
    matrix_byte_budget = int(budget_mib * 1024 * 1024) // 8
    per_row_budget = len(features) * 8 + 64
    capacity_rows = matrix_byte_budget // per_row_budget
    if capacity_rows < 2:
        raise PlatformError("SOS-ML-TRAIN-CAPACITY")

    sampled = input_policy.mode == "sample"
    target_rows = input_policy.rows if sampled else capacity_rows
    assert target_rows is not None
    if target_rows > capacity_rows:
        raise PlatformError("SOS-ML-TRAIN-CAPACITY")
    chunk_rows = max(1, min(8192, (matrix_byte_budget // 4) // (len(features) * 8)))
    capacity = min(target_rows, chunk_rows)
    selected = np.empty((capacity, len(features)), dtype=np.float64)
    selected_priority = np.empty(capacity, dtype=np.uint64) if sampled else None
    selected_rows = 0
    source_rows = 0
    rng = np.random.default_rng(input_policy.seed) if sampled else None

    with pinned_scan_dataset(store, dataset) as source:
        schema = source.collect_schema()
        if any(name not in schema for name in features):
            raise ValueError("Unknown training feature")
        if any(schema[name] not in NUMERIC for name in features):
            raise ValueError("Only numeric training features are supported")
        scan = source.select(features)
        frames = (
            canonical_sorted_batches(
                scan, features, schema, chunk_rows=chunk_rows, budget_mib=budget_mib
            )
            if sampled and selection_algorithm_version == TRAINING_SELECTION_CURRENT
            else scan.collect_batches(chunk_size=chunk_rows, maintain_order=True)
        )
        for frame in frames:
            source_rows += frame.height
            if not sampled and source_rows > capacity_rows:
                # 전체 사용 정책을 메모리 사정에 따라 조용히 부분집합으로 변경하지 않는다.
                raise PlatformError("SOS-ML-TRAIN-CAPACITY")
            if any(frame.null_count().row(0)):
                raise ValueError("Training features contain null values")
            for name in features:
                if schema[name] in INTEGER_DTYPES:
                    values = frame[name]
                    if values.max() > 2**53 or values.min() < -(2**53):
                        raise ValueError("Integer feature exceeds the exact float64 range")
            matrix = np.asarray(frame.to_numpy(), dtype=np.float64)
            if not np.isfinite(matrix).all():
                raise ValueError("Training features must be finite numeric values")
            priority = (
                rng.integers(0, np.iinfo(np.uint64).max, size=frame.height, dtype=np.uint64)
                if rng is not None
                else None
            )
            incoming = min(frame.height, target_rows - selected_rows)
            if incoming:
                needed = selected_rows + incoming
                if needed > capacity:
                    capacity = min(target_rows, max(needed, capacity * 2))
                    grown = np.empty((capacity, len(features)), dtype=np.float64)
                    grown[:selected_rows] = selected[:selected_rows]
                    selected = grown
                    if selected_priority is not None:
                        grown_priority = np.empty(capacity, dtype=np.uint64)
                        grown_priority[:selected_rows] = selected_priority[:selected_rows]
                        selected_priority = grown_priority
                selected[selected_rows:needed] = matrix[:incoming]
                if selected_priority is not None and priority is not None:
                    selected_priority[selected_rows:needed] = priority[:incoming]
                selected_rows = needed
            if not sampled or selected_rows < target_rows or incoming == frame.height:
                continue
            assert selected_priority is not None and priority is not None
            matrix = matrix[incoming:]
            priority = priority[incoming:]
            candidates = np.flatnonzero(priority < selected_priority[:selected_rows].max())
            if not candidates.size:
                continue
            combined_priority = np.concatenate((
                selected_priority[:selected_rows], priority[candidates]
            ))
            chosen = np.argpartition(combined_priority, target_rows - 1)[:target_rows]
            keep = np.zeros(target_rows, dtype=bool)
            keep[chosen[chosen < target_rows]] = True
            dropped = np.flatnonzero(~keep)
            replacements = candidates[chosen[chosen >= target_rows] - target_rows]
            selected[dropped] = matrix[replacements]
            selected_priority[dropped] = priority[replacements]
    if source_rows < 2:
        raise ValueError("Training requires at least two rows")
    if sampled and source_rows < target_rows:
        raise ValueError("Sample rows exceed Dataset row count")

    # 기존 학습의 Feature 순서 정렬 계약을 유지한다.
    order = np.lexsort(selected[:selected_rows].T[::-1])
    selected = np.ascontiguousarray(selected[order])
    selection = {
        "source_rows": source_rows,
        "selected_rows": len(selected),
        "method": selection_algorithm_version if sampled else "all_rows",
        "algorithm_version": selection_algorithm_version if sampled else "all_rows_v1",
        "requested_rows": input_policy.rows,
        "seed": input_policy.seed,
        "selection_sha256": hashlib.sha256(selected.tobytes()).hexdigest(),
    }
    return selected, selection


def train_dataset(store, dataset: dict, spec: dict, pending: Path) -> dict:
    """자식 Worker에서 학습 입력을 확정하고 모델과 실행 근거를 기록한다."""
    if dataset["version"] != spec["dataset_version"]:
        raise ValueError("Dataset version mismatch")
    if spec["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model algorithm")
    features = spec["features"]
    config = IsolationForestConfig(**spec["params"])
    input_policy = MLTrainingInputPolicy.model_validate(spec.get("training_input", {}))
    matrix, selection = training_matrix(
        store, dataset, features, input_policy=input_policy,
        # 정책/알고리즘 버전은 제출된 Job에 고정한다. 이 필드가 없는 기존
        # Job은 전환 이전의 읽기 순서 기반 선택으로 실행한다.
        selection_algorithm_version=spec.get(
            "selection_algorithm_version", TRAINING_SELECTION_LEGACY
        ),
    )
    with pinned_scan_dataset(store, dataset) as source:
        schema = source.collect_schema()
        feature_dtypes = [str(schema[name]) for name in features]
    model = train_isolation_forest(matrix, config)
    scores = model.score_anomalies(matrix)
    threshold = quantile_threshold(scores, 0.05)
    summary = evaluate_anomaly_scores(scores, threshold=threshold)
    summary["scope"] = "training_data_only"
    summary["threshold_source"] = "training_score_quantile_0.95"
    joblib.dump(model.estimator, pending)
    return {
        "algorithm": "isolation_forest",
        "dataset_version": dataset["version"],
        "features": features,
        "params": asdict(config),
        "source_rows": selection["source_rows"],
        "training_rows": len(scores),
        "training_selection": selection,
        "training_score_summary": summary,
        "runtime": {
            "model_format": "joblib",
            "loading": "trusted_worker_only",
            "feature_dtypes": feature_dtypes,
            "feature_reference_medians": np.median(matrix, axis=0).tolist(),
            "training_selection": selection,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "polars": pl.__version__,
        },
    }
