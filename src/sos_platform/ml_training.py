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

    입력 스캔 순서의 각 행에 Seed 고정 무작위 우선순위를 부여하고 전역 bottom-k를
    선택한다. Feature 값이 같은 별개 관측도 독립적으로 선택된다. 동일한 판본을
    동일한 행 순서로 읽으면 같은 입력을 재현하며, 스캔 순서가 달라지면 선택
    결과가 달라질 수 있으므로 실제 선택 행렬의 SHA-256을 기록한다.
    표본이 업무 집단·시간 구간을 대표한다는 보장은 없다.

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
    # 선택 행의 모델 점수와 Python 분위수 계산 객체에도 여유를 예약한다.
    # Feature가 1개인 입력도 행렬 바이트만 계산해 과다 수용하지 않는다.
    per_row_budget = len(features) * 8 + 64
    max_rows = matrix_byte_budget // per_row_budget
    if max_rows < 2:
        raise ValueError("Training feature width exceeds the Worker memory budget")
    chunk_rows = max(1, min(8192, (matrix_byte_budget // 4) // (len(features) * 8)))

    # 초기에는 한 배치만 예약하고, 필요할 때만 배가시켜 최대 입력 예산까지 확장.
    capacity = min(max_rows, chunk_rows)
    selected = np.empty((capacity, len(features)), dtype=np.float64)
    selected_priority = np.empty(capacity, dtype=np.uint64)
    selected_rows = 0
    source_rows = 0
    # 원본 행 순서에 따라 독립 우선순위를 부여해 값이 중복된 관측의 빈도를 보존.
    rng = np.random.default_rng(seed)
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
            priority = rng.integers(
                0, np.iinfo(np.uint64).max, size=frame.height, dtype=np.uint64
            )
            incoming = min(frame.height, max_rows - selected_rows)
            if incoming:
                needed = selected_rows + incoming
                if needed > capacity:
                    capacity = min(max_rows, max(needed, capacity * 2))
                    grown = np.empty((capacity, len(features)), dtype=np.float64)
                    grown[:selected_rows] = selected[:selected_rows]
                    selected = grown
                    grown_priority = np.empty(capacity, dtype=np.uint64)
                    grown_priority[:selected_rows] = selected_priority[:selected_rows]
                    selected_priority = grown_priority
                selected[selected_rows:needed] = matrix[:incoming]
                selected_priority[selected_rows:needed] = priority[:incoming]
                selected_rows = needed
            if selected_rows < max_rows or incoming == frame.height:
                continue
            matrix = matrix[incoming:]
            priority = priority[incoming:]
            # 가장 나쁜 현재 우선순위보다 좋은 새 행만 후보로 삼는다.
            candidates = np.flatnonzero(priority < selected_priority[:selected_rows].max())
            if not candidates.size:
                continue
            combined_priority = np.concatenate((
                selected_priority[:selected_rows], priority[candidates]
            ))
            # 선택된 전체 Feature 행렬을 매번 복사하지 않는다. 우선순위만
            # 재계산하고 교체가 필요한 행만 기존 버퍼에 덮어쓴다.
            chosen = np.argpartition(combined_priority, max_rows - 1)[:max_rows]
            keep = np.zeros(max_rows, dtype=bool)
            keep[chosen[chosen < max_rows]] = True
            dropped = np.flatnonzero(~keep)
            replacements = candidates[chosen[chosen >= max_rows] - max_rows]
            selected[dropped] = matrix[replacements]
            selected_priority[dropped] = priority[replacements]
    if source_rows < 2:
        raise ValueError("Training requires at least two rows")

    # 기존의 Feature 값 정렬 계약을 유지한다. 전체 입력과 부분집합 모두
    # 모델의 행 위치 기반 표본 추출이 배치 경계나 선택 우선순위에 종속되지 않는다.
    order = np.lexsort(selected[:selected_rows].T[::-1])
    selected = np.ascontiguousarray(selected[order])
    selection = {
        "source_rows": source_rows,
        "selected_rows": len(selected),
        "method": "all_rows" if source_rows == len(selected) else "row_priority_bottom_k_v1",
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
