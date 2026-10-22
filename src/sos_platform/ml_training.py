"""Isolation Forest의 명시된 학습 입력을 실행 예산 안에서 재현 가능하게 선택한다."""

import hashlib
import os
import platform
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import joblib
import numpy as np
import polars as pl
import sklearn

from .datasets import pinned_scan_dataset
from .errors import PlatformError
from .execution_budget import ExecutionBudget
from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from .ml_if_preprocessing import FittedIFMedianImputer, _train_median, fit_if_median_imputer
from .ml_input import (
    INTEGER_DTYPES,
    NUMERIC,
    TRAINING_SELECTION_CURRENT,
    TRAINING_SELECTION_LEGACY,
)
from .ml_isolation_forest import IsolationForestConfig, train_isolation_forest
from .ml_sampling import canonical_sorted_batches
from .ml_temporal_split import split_training_source
from .schemas import MLTemporalSplitSpec, MLTrainingInputPolicy


def training_matrix(
    store,
    dataset: dict,
    features: list[str],
    *,
    input_policy: MLTrainingInputPolicy,
    worker_memory_mib: int | None = None,
    selection_algorithm_version: str = TRAINING_SELECTION_CURRENT,
    missing_policy: str = "reject",
    temporal_split: MLTemporalSplitSpec | None = None,
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
    if missing_policy not in ("reject", "train_median"):
        raise ValueError("Unsupported training missing policy")
    if temporal_split is not None and temporal_split.timestamp_column in features:
        raise ValueError("Temporal split timestamp cannot be a training feature")
    if selection_algorithm_version not in (
        TRAINING_SELECTION_CURRENT, TRAINING_SELECTION_LEGACY
    ):
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

    temporal_evidence = None
    with pinned_scan_dataset(store, dataset) as source:
        if temporal_split is not None:
            source, temporal_evidence = split_training_source(
                source,
                timestamp_column=temporal_split.timestamp_column,
                train_end=temporal_split.train_end,
                calibration_end=temporal_split.calibration_end,
            )
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
        try:
            for frame in frames:
                source_rows += frame.height
                if not sampled and source_rows > capacity_rows:
                    # 전체 사용 정책을 메모리 사정에 따라 조용히 부분집합으로 변경하지 않는다.
                    raise PlatformError("SOS-ML-TRAIN-CAPACITY")
                if missing_policy == "reject" and any(frame.null_count().row(0)):
                    raise ValueError("Training features contain null values")
                for name in features:
                    if schema[name] in INTEGER_DTYPES:
                        values = frame[name]
                        maximum, minimum = values.max(), values.min()
                        # 전부 null인 Batch는 극값이 None이다. Train 전체에는 값이
                        # 있을 수 있으므로 전처리 단계에서 결측 정책을 판단한다.
                        if (
                            (maximum is not None and maximum > 2**53)
                            or (minimum is not None and minimum < -(2**53))
                        ):
                            raise ValueError("Integer feature exceeds the exact float64 range")
                matrix = np.asarray(frame.to_numpy(), dtype=np.float64)
                if missing_policy == "reject" and not np.isfinite(matrix).all():
                    raise ValueError("Training features must be finite numeric values")
                if missing_policy == "train_median" and np.isinf(matrix).any():
                    raise ValueError("Training features contain infinity")
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
        finally:
            # 선택·검증 오류가 발생해도 DuckDB 연결과 Attempt Spill을 즉시 닫는다.
            close_frames = getattr(frames, "close", None)
            if close_frames is not None:
                close_frames()
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
    if temporal_evidence is not None:
        selection["temporal_split"] = temporal_evidence
    return selected, selection


@contextmanager
def staged_all_training_matrix(
    store,
    dataset: dict,
    features: list[str],
    *,
    missing_policy: str,
    temporal_split: MLTemporalSplitSpec | None,
):
    """모든 Train 행을 선택하되 행렬은 Attempt 전용 디스크에 유지한다.

    모델의 `all`은 일부를 임의로 버리지 않는다. Train 행 수와 실행 예산을
    선택 정책으로 혼동하지 않으며, 실제 RSS/Stage 한도는 Worker가 감시한다.

    Args:
        store: 핀 고정 Dataset을 읽는 Store.
        dataset: 불변 Dataset 참조.
        features: 순서 고정 Numeric Feature.
        missing_policy: reject 또는 train_median.
        temporal_split: 명시적인 Train 시간 경계 또는 None.

    Yields:
        (읽기 전용 정렬된 Memmap 행렬, 선택 근거).

    Raises:
        ValueError: 입력 컬럼, 타입, 결측 또는 행 수 불일치.
    """
    if not features or len(set(features)) != len(features):
        raise ValueError("Training features must be nonempty and distinct")
    if missing_policy not in ("reject", "train_median"):
        raise ValueError("Unsupported training missing policy")
    if temporal_split is not None and temporal_split.timestamp_column in features:
        raise ValueError("Temporal split timestamp cannot be a training feature")
    root = os.environ.get("POLARS_TEMP_DIR")
    if root is not None and (Path(root).is_symlink() or not Path(root).is_dir()):
        raise ValueError("Worker training Stage directory is unavailable")
    with TemporaryDirectory(prefix="all-input-", dir=root) as directory:
        stage = Path(directory)
        with pinned_scan_dataset(store, dataset) as source:
            evidence = None
            if temporal_split is not None:
                source, evidence = split_training_source(
                    source,
                    timestamp_column=temporal_split.timestamp_column,
                    train_end=temporal_split.train_end,
                    calibration_end=temporal_split.calibration_end,
                )
            schema = source.collect_schema()
            if any(name not in schema for name in features):
                raise ValueError("Unknown training feature")
            if any(schema[name] not in NUMERIC for name in features):
                raise ValueError("Only numeric training features are supported")
            scan = source.select(features)
            count = scan.select(pl.len()).collect().item()
            if count < 2:
                raise ValueError("Training requires at least two rows")
            raw_path = stage / "input.float64"
            raw = np.memmap(raw_path, mode="w+", dtype=np.float64, shape=(count, len(features)))
            at = 0
            frames = scan.collect_batches(chunk_size=8192, maintain_order=True)
            try:
                for frame in frames:
                    n = frame.height
                    if at + n > count:
                        raise ValueError("Dataset changed during pinned training read")
                    if missing_policy == "reject" and any(frame.null_count().row(0)):
                        raise ValueError("Training features contain null values")
                    for name in features:
                        if schema[name] in INTEGER_DTYPES:
                            values = frame[name]
                            maximum, minimum = values.max(), values.min()
                            if (
                                (maximum is not None and maximum > 2**53)
                                or (minimum is not None and minimum < -(2**53))
                            ):
                                raise ValueError("Integer feature exceeds the exact float64 range")
                    values = np.asarray(frame.to_numpy(), dtype=np.float64)
                    if missing_policy == "reject" and not np.isfinite(values).all():
                        raise ValueError("Training features must be finite numeric values")
                    if missing_policy == "train_median" and np.isinf(values).any():
                        raise ValueError("Training features contain infinity")
                    raw[at:at + n] = values
                    at += n
            finally:
                close = getattr(frames, "close", None)
                if close is not None:
                    close()
            if at != count:
                raise ValueError("Dataset changed during pinned training read")
        raw.flush()
        # 기존 all_rows_v1은 전체 행렬을 Feature 순서로 정렬한 뒤 학습한다.
        # NumPy 인덱스만 메모리에 보관하고 결과 행렬은 디스크에서 청크별 재배치한다.
        order = np.lexsort(raw.T[::-1])
        sorted_path = stage / "sorted.float64"
        selected = np.memmap(
            sorted_path, mode="w+", dtype=np.float64, shape=(count, len(features))
        )
        digest = hashlib.sha256()
        for start in range(0, count, 8192):
            end = min(start + 8192, count)
            selected[start:end] = raw[order[start:end]]
            digest.update(selected[start:end].tobytes())
        selected.flush()
        del raw
        raw_path.unlink()
        del order
        selection = {
            "source_rows": count,
            "selected_rows": count,
            "method": "all_rows",
            "algorithm_version": "all_rows_v1",
            "requested_rows": None,
            "seed": None,
            "selection_sha256": digest.hexdigest(),
        }
        if evidence is not None:
            selection["temporal_split"] = evidence
        yield selected, selection


def _fit_staged_medians(
    matrix: np.ndarray, features: list[str]
) -> FittedIFMedianImputer:
    """열마다 독립 중앙값을 Fit하고 전체 2D 배열 사본 생성을 피한다."""
    medians = []
    for col in range(matrix.shape[1]):
        column = matrix[:, col]
        if np.isnan(column).all():
            raise ValueError("Entirely missing training features require explicit exclusion")
        medians.append(_train_median(column))
    return FittedIFMedianImputer(tuple(features), tuple(medians))


def _staged_model_input(
    matrix: np.ndarray,
    preprocessing: FittedIFMedianImputer,
    *,
    stage: Path,
) -> np.memmap:
    """Fit 상태를 고정하고 Chunk마다 결측을 치환한 Model 입력을 디스크에 기록한다."""
    output = np.memmap(
        stage / "transformed.float64", mode="w+", dtype=np.float64, shape=matrix.shape
    )
    for start in range(0, matrix.shape[0], 8192):
        end = min(start + 8192, matrix.shape[0])
        output[start:end] = preprocessing.transform(
            matrix[start:end], feature_names=preprocessing.feature_names
        )
    output.flush()
    return output


def _training_score_summary(model, matrix: np.ndarray) -> tuple[dict, int]:
    """점수 계산을 배치로 제한하고 임계값 의미는 기존 Train-only 그대로 둔다."""
    scores = []
    for start in range(0, matrix.shape[0], 8192):
        end = min(start + 8192, matrix.shape[0])
        scores.extend(model.score_anomalies(matrix[start:end]))
    threshold = quantile_threshold(scores, 0.05)
    summary = evaluate_anomaly_scores(scores, threshold=threshold)
    summary["scope"] = "training_data_only"
    summary["threshold_source"] = "training_score_quantile_0.95"
    return summary, len(scores)


def train_dataset(store, dataset: dict, spec: dict, pending: Path) -> dict:
    """자식 Worker에서 학습 입력을 확정하고 모델과 실행 근거를 기록한다."""
    if dataset["version"] != spec["dataset_version"]:
        raise ValueError("Dataset version mismatch")
    if spec["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model algorithm")
    features = spec["features"]
    config = IsolationForestConfig(**spec["params"])
    input_policy = MLTrainingInputPolicy.model_validate(spec.get("training_input", {}))
    temporal_spec = (
        MLTemporalSplitSpec.model_validate(spec["temporal_split"])
        if spec.get("temporal_split") is not None else None
    )
    missing_policy = spec.get("missing_policy", "reject")
    # Input selection은 모델링 의미이며 물리적인 데이터 적재 방식과 별개다.
    # all은 디스크 기반 전체 입력을, sample은 기존 결정적 선택 계약을 사용한다.
    if input_policy.mode == "all":
        selected_context = staged_all_training_matrix(
            store, dataset, features,
            missing_policy=missing_policy, temporal_split=temporal_spec,
        )
    else:
        selected_context = nullcontext(training_matrix(
            store, dataset, features, input_policy=input_policy,
            missing_policy=missing_policy, temporal_split=temporal_spec,
            selection_algorithm_version=spec.get(
                "selection_algorithm_version", TRAINING_SELECTION_LEGACY
            ),
        ))
    with selected_context as (matrix, selection):
        with pinned_scan_dataset(store, dataset) as source:
            schema = source.collect_schema()
            feature_dtypes = [str(schema[name]) for name in features]
        if input_policy.mode == "all":
            preprocessing = _fit_staged_medians(matrix, features)
            # Parent Training Stage가 지정돼 있으면 같은 Attempt 아래에 생성한다.
            scratch_root = os.environ.get("POLARS_TEMP_DIR")
            with TemporaryDirectory(prefix="all-prepared-", dir=scratch_root) as directory:
                transformed = _staged_model_input(
                    matrix, preprocessing, stage=Path(directory)
                )
                model = train_isolation_forest(transformed, config)
                summary, count = _training_score_summary(model, transformed)
        else:
            preprocessing = fit_if_median_imputer(matrix, feature_names=features)
            transformed = preprocessing.transform(matrix, feature_names=features)
            model = train_isolation_forest(transformed, config)
            summary, count = _training_score_summary(model, transformed)
    joblib.dump(
        {
            "schema": "if_model_with_preprocessing_v1",
            "estimator": model.estimator,
            "preprocessing": preprocessing,
        },
        pending,
    )
    return {
        "algorithm": "isolation_forest",
        "dataset_version": dataset["version"],
        "features": features,
        "params": asdict(config),
        "source_rows": selection["source_rows"],
        "training_rows": count,
        "training_selection": selection,
        "training_score_summary": summary,
        "runtime": {
            "model_format": "joblib",
            "artifact_schema": "if_model_with_preprocessing_v1",
            "loading": "trusted_worker_only",
            "feature_dtypes": feature_dtypes,
            "feature_reference_medians": list(preprocessing.medians),
            "preprocessing_mode": "train_median_v1",
            "training_missing_policy": missing_policy,
            "training_selection": selection,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "polars": pl.__version__,
        },
    }
