"""불변 Dataset의 전체 또는 UTC 시간 구간을 신뢰된 Model Revision으로 점수화한다.

입력은 임시 Parquet에 Streaming Stage하고 일정 크기의 RecordBatch로 추론한다.
Scoring Result의 row_index는 결과 파일의 행 번호이지 원본 Dataset의 ID가 아니다.
"""

import math
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import polars as pl
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score, roc_auc_score

from .datasets import pinned_scan_dataset
from .ml_input import INTEGER_DTYPES, NUMERIC
from .ml_runtime import load_trusted_isolation_forest
from .ml_temporal_split import select_temporal_partition
from .schemas import MLScoringTemporalScope

SCORING_BATCH_ROWS = 10_000


def _scoring_scope(source: pl.LazyFrame, dataset: dict, model: dict, raw_scope: dict):
    """모델 학습과 같은 불변 데이터 판본·분할 정의만 선택한다."""
    scope = MLScoringTemporalScope.model_validate(raw_scope)
    if dataset["id"] != model["dataset_id"] or dataset["version"] != model["dataset_version"]:
        raise ValueError("Temporal Scoring requires the original training Dataset revision")
    training = model["runtime"].get("training_selection", {}).get("temporal_split")
    if not isinstance(training, dict):
        raise ValueError("Model Revision has no pinned temporal training split")
    expected = (
        training["timestamp_column"],
        training["train_end"],
        training["calibration_end"],
    )
    requested = (
        scope.timestamp_column,
        scope.train_end.isoformat(),
        scope.calibration_end.isoformat(),
    )
    # 같은 시간대를 가진 다른 UTC offset도 동일한 순간이면 호환된다.
    from datetime import timezone

    requested = (
        requested[0],
        scope.train_end.astimezone(timezone.utc).isoformat(),
        scope.calibration_end.astimezone(timezone.utc).isoformat(),
    )
    if requested != expected:
        raise ValueError("Temporal Scoring must use Model Revision training boundaries")
    frame, evidence = select_temporal_partition(
        source,
        timestamp_column=scope.timestamp_column,
        train_end=scope.train_end,
        calibration_end=scope.calibration_end,
        partition=scope.partition,
    )
    return frame, evidence


def _score_summary(score_sums: list[float], count: int, low: float, high: float) -> dict:
    """모든 점수를 Python 목록에 보관하지 않고 기본 분포 통계를 계산한다."""
    return {
        "score_direction": "higher_is_more_anomalous",
        "score_count": count,
        "score_min": float(low),
        "score_max": float(high),
        "score_mean": math.fsum(score_sums) / count,
        "threshold": None,
        "predicted_anomalies": None,
        "alert_rate": None,
        "evaluation_kind": "unlabeled",
    }


def run_dataset_scoring(store, dataset: dict, spec: dict, pending: Path) -> dict:
    """신뢰된 모델로 대상 전체/보정·평가 구간을 분할 점수화해 Parquet에 게시한다.

    Args:
        store: 고정 Dataset·Model Revision을 조회하는 저장소.
        dataset: Job에 고정된 Dataset 판본.
        spec: 모델 판본과 선택적 UTC 기간, 레이블 컬럼.
        pending: Attempt가 전용으로 소유하는 미게시 결과 경로.

    Returns:
        dict: Scoring Result 영속 계약에 맞춘 점수 방향·행 수·근거.
    """
    if dataset["version"] != spec["dataset_version"]:
        raise ValueError("Scoring Dataset revision changed")
    model = store.get_model_revision(spec["model_revision_id"])
    if model is None or model["id"] != spec["model_revision_id"]:
        raise ValueError("Model Revision is unavailable")
    if model["owner_actor"] != spec["model_owner_actor"]:
        raise ValueError("Model ownership changed")
    features = model["features"]
    label = spec.get("label_column")
    if label is not None and label in features:
        raise ValueError("Label column must not be a training feature")
    fields = [*features, *([label] if label else [])]
    scope_evidence = None
    pending.parent.mkdir(parents=True, exist_ok=True)
    # 중간 파일의 수명은 Job Attempt에 한정한다. 데이터 전체는 메모리에 올리지 않는다.
    with TemporaryDirectory(prefix="ml-score-", dir=pending.parent) as directory:
        staged = Path(directory) / "selected.parquet"
        with pinned_scan_dataset(store, dataset) as source:
            if spec.get("temporal_scope") is not None:
                source, scope_evidence = _scoring_scope(
                    source, dataset, model, spec["temporal_scope"]
                )
            schema = source.collect_schema()
            if any(name not in schema for name in fields):
                raise ValueError("Scoring feature or label is missing")
            if any(schema[name] not in NUMERIC for name in features):
                raise ValueError("Scoring features must be numeric")
            if [str(schema[name]) for name in features] != model["runtime"].get("feature_dtypes"):
                raise ValueError("Scoring feature dtypes differ from Model Revision")
            source.select(fields).sink_parquet(staged)

        model_adapter = load_trusted_isolation_forest(model, store.home / "cache")
        supports_missing = (
            model["runtime"].get("artifact_schema") == "if_model_with_preprocessing_v1"
        )
        count = 0
        score_sums: list[float] = []
        low = math.inf
        high = -math.inf
        all_scores: list[float] = []
        all_labels: list[int] = []
        writer = None
        try:
            with pq.ParquetFile(staged) as reader:
                for batch in reader.iter_batches(batch_size=SCORING_BATCH_ROWS):
                    frame = pl.from_arrow(batch)
                    if frame.height == 0:
                        continue
                    if not supports_missing and any(frame.select(features).null_count().row(0)):
                        raise ValueError("Scoring features contain null values")
                    for name in features:
                        if schema[name] in INTEGER_DTYPES:
                            series = frame[name]
                            if series.max() > 2**53 or series.min() < -(2**53):
                                raise ValueError("Integer feature exceeds the exact float64 range")
                    matrix = frame.select(features).to_numpy()
                    scores = model_adapter.score_anomalies(matrix)
                    if not all(math.isfinite(s) for s in scores):
                        raise ValueError("Non-finite anomaly scores")
                    n = frame.height
                    low = min(low, min(scores))
                    high = max(high, max(scores))
                    score_sums.append(math.fsum(scores))
                    result = pl.DataFrame({
                        "row_index": np.arange(count, count + n, dtype=np.int64),
                        "anomaly_score": scores,
                    }).with_columns(frame.select(features).to_struct("features"))
                    if label is not None:
                        if frame[label].null_count():
                            raise ValueError("Labeled scoring requires complete binary labels")
                        labels = frame[label].to_list()
                        if any(
                            type(value) not in (int, float) or value not in (0, 1)
                            for value in labels
                        ):
                            raise ValueError("Scoring labels must be binary 0/1")
                        labels = [int(x) for x in labels]
                        all_labels.extend(labels)
                        all_scores.extend(scores)
                        result = result.with_columns(
                            pl.Series(name="label", values=labels, dtype=pl.Int8)
                        )
                    arrow = result.to_arrow()
                    if writer is None:
                        writer = pq.ParquetWriter(pending, arrow.schema)
                    writer.write_table(arrow)
                    count += n
        finally:
            if writer is not None:
                writer.close()
        if count == 0:
            raise ValueError("Scoring requires at least one row")
        if scope_evidence is not None and count != scope_evidence[f"{scope_evidence['partition']}_rows"]:
            raise ValueError("Scoring partition row count differs from pinned temporal evidence")
        score_summary = _score_summary(score_sums, count, low, high)
        score_summary["scope"] = "scoring_dataset"
        score_summary["score_scale"] = "negative_sklearn_score_samples"
        same_input_revision = (
            model.get("dataset_id") == dataset.get("id")
            and model.get("dataset_version") == dataset["version"]
        )
        score_summary["training_data_reused"] = (
            same_input_revision and scope_evidence is None
        )
        if scope_evidence is not None:
            score_summary["temporal_scope"] = {
                **scope_evidence,
                "dataset_id": dataset["id"],
                "dataset_version": dataset["version"],
                "model_revision_id": model["id"],
            }

        ranked_metrics = None
        if label is not None:
            positives = sum(all_labels)
            ranked_metrics = {
                "scope": "fully_labeled_scoring_dataset",
                "label_column": label,
                "positive_count": positives,
                "negative_count": count - positives,
                "average_precision": None,
                "roc_auc": None,
            }
            if 0 < positives < count:
                ranked_metrics["average_precision"] = float(
                    average_precision_score(all_labels, all_scores)
                )
                ranked_metrics["roc_auc"] = float(roc_auc_score(all_labels, all_scores))
        return {
            "model_revision_id": model["id"],
            "dataset_version": dataset["version"],
            "feature_names": features,
            "row_count": count,
            "score_direction": "higher_is_more_anomalous",
            "score_summary": score_summary,
            "ranking_metrics": ranked_metrics,
            "label_column": label,
        }
