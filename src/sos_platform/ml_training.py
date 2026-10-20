"""Isolation Forest 학습 입력을 전체 Dataset 판본에서 메모리 예산 내로 선택한다."""

import hashlib
import platform
from dataclasses import asdict
from pathlib import Path

import joblib
import numpy as np
import polars as pl
import sklearn

from .datasets import pinned_scan_dataset
from .execution_budget import ExecutionBudget
from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from .ml_input import INTEGER_DTYPES, NUMERIC
from .ml_isolation_forest import IsolationForestConfig, train_isolation_forest


def training_matrix(
    store,
    dataset: dict,
    features: list[str],
    *,
    seed: int,
    worker_memory_mib: int | None = None,
) -> tuple[np.ndarray, dict]:
    """Dataset 전체를 확인하며 실행 RSS 예산에서 유도한 행렬 크기만 유지한다.

    선택 기준은 Feature 값의 고정 Seed 해시이다. 전체 행을 검사한 뒤 우선순위가
    가장 낮은 행만 유지하므로 소스 스캔의 배치 크기·순서에 영향을 받지 않는다.
    이 선택은 층화/시간 기반 표본이나 통계적 대표성을 보장하지 않는다.

    Args:
        store: 원본 Dataset을 고정해서 읽을 저장소.
        dataset: 판본을 포함한 Dataset 참조.
        features: 순서가 확정된 숫자형 Feature 목록.
        seed: 실행 설정에 기록될 결정적인 선택 Seed.
        worker_memory_mib: 실제 Worker의 RSS 예산. 테스트에서는 별도로 지정할 수 있다.

    Returns:
        학습용 float64 행렬 및 Dataset 입력 선택 근거.
    """
    if not features or len(set(features)) != len(features):
        raise ValueError("Training features must be nonempty and distinct")
    memory_mib = (
        ExecutionBudget.from_env().max_rss_mib
        if worker_memory_mib is None
        else worker_memory_mib
    )
    if memory_mib <= 0:
        raise ValueError("Training memory budget must be positive")
    # 모델 검증·학습·점수 계산의 추가 메모리를 위해 RSS의 1/8만 원본 행렬에 할당.
    # 실제 메모리 제한은 부모 Worker의 감시/cgroup 정책이 별도로 집행한다.
    matrix_byte_budget = int(memory_mib * 1024 * 1024) // 8
    max_rows = matrix_byte_budget // (len(features) * 8)
    if max_rows < 2:
        raise ValueError("Training feature width exceeds the Worker memory budget")
    chunk_rows = max(1, min(8192, (matrix_byte_budget // 4) // (len(features) * 8)))

    selected = np.empty((0, len(features)), dtype=np.float64)
    selected_priority = np.empty(0, dtype=np.uint64)
    source_rows = 0
    with pinned_scan_dataset(store, dataset) as source:
        schema = source.collect_schema()
        if any(name not in schema for name in features):
            raise ValueError("Unknown training feature")
        if any(schema[name] not in NUMERIC for name in features):
            raise ValueError("Only numeric training features are supported")
        for frame in source.select(features).collect_batches(
            chunk_size=chunk_rows, maintain_order=True
        ):
            source_rows += frame.height
            if any(frame.null_count().row(0)):
                raise ValueError("Training features contain null values")
            # 정수형 Feature를 float64로 변환하기 전에 전체 입력에서 정밀도 검사.
            for name in features:
                if schema[name] in INTEGER_DTYPES:
                    values = frame[name]
                    if values.max() > 2**53 or values.min() < -(2**53):
                        raise ValueError("Integer feature exceeds the exact float64 range")
            matrix = np.asarray(frame.to_numpy(), dtype=np.float64)
            if not np.isfinite(matrix).all():
                raise ValueError("Training features must be finite numeric values")
            priority = (
                frame.select(pl.struct(features).hash(seed=seed)).to_series().to_numpy()
            )
            # 이미 작은 우선순위만 남아 있다면 불필요한 배열 복사를 피한다.
            if selected_priority.size == max_rows:
                keep = priority <= selected_priority.max()
                matrix, priority = matrix[keep], priority[keep]
            if not len(priority):
                continue
            combined = np.concatenate((selected, matrix))
            combined_priority = np.concatenate((selected_priority, priority))
            if len(combined_priority) > max_rows:
                indices = np.argpartition(combined_priority, max_rows - 1)[:max_rows]
                selected = combined[indices]
                selected_priority = combined_priority[indices]
            else:
                selected = combined
                selected_priority = combined_priority
    if source_rows < 2:
        raise ValueError("Training requires at least two rows")

    # Priority 동점은 동일 Feature 값에서 생기며 정렬 전후의 계산 의미가 같다.
    order = np.argsort(selected_priority, kind="stable")
    selected = np.ascontiguousarray(selected[order])
    selection = {
        "source_rows": source_rows,
        "selected_rows": len(selected),
        "method": "all_rows" if source_rows == len(selected) else "feature_hash_bottom_k_v1",
        "seed": seed,
        "matrix_byte_budget": matrix_byte_budget,
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
    matrix, selection = training_matrix(
        store, dataset, features, seed=config.random_state
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
