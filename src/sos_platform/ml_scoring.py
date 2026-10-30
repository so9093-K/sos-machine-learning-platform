"""불변 Dataset의 전체 또는 UTC 시간 구간을 신뢰된 Model Revision으로 점수화한다.

입력은 임시 Parquet에 Streaming Stage하고 일정 크기의 RecordBatch로 추론한다.
Scoring Result의 row_index는 결과 파일의 행 번호이지 원본 Dataset의 ID가 아니다.
"""

import math
from datetime import UTC
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import polars as pl
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score, roc_auc_score

from .datasets import pinned_scan_dataset
from .ml_anomaly import histogram_edges
from .ml_input import INTEGER_DTYPES, model_input_issues
from .ml_observation_identity import validate_observation_identity
from .ml_outputs import require_output_contract
from .ml_runtime import load_trusted_isolation_forest
from .ml_scoring_staging import scoring_stage_prefix
from .ml_temporal_split import select_temporal_partition, utc_observation_time
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

    requested = (
        requested[0],
        scope.train_end.astimezone(UTC).isoformat(),
        scope.calibration_end.astimezone(UTC).isoformat(),
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


def _score_summary(score_sums: list[float], count: int, low: float, high: float,
                   direction: str) -> dict:
    """모든 점수를 Python 목록에 보관하지 않고 기본 분포 통계를 계산한다."""
    return {
        "score_direction": direction,
        "score_count": count,
        "score_min": float(low),
        "score_max": float(high),
        "score_mean": math.fsum(score_sums) / count,
        "threshold": None,
        "predicted_anomalies": None,
        "alert_rate": None,
        "evaluation_kind": "unlabeled",
    }


def histogram_from_scoring_file(
    path: Path, *, low: float, high: float, expected_rows: int, bins: int = 32,
) -> dict:
    """불변 게시 전 Parquet의 실제 점수만 일정 배치로 재조회해 정확한 구간 빈도 기록.

    Scoring 요약의 min/max는 이미 전체 Batch에서 계산됐으므로 구간 폭 결정에는
    별도 데이터 스캔이 필요하지 않다. 추가 조회도 메모리에 모든 행을 적재하지 않는다.
    """
    edges = histogram_edges(low, high, bins=bins)
    counts = np.zeros(bins, dtype=np.int64)
    inspected = 0
    with pq.ParquetFile(path) as reader:
        for batch in reader.iter_batches(batch_size=8192, columns=["anomaly_score"]):
            values = batch.column(0).to_numpy(zero_copy_only=False)
            if not np.isfinite(values).all():
                raise ValueError("Histogram encountered non-finite anomaly scores")
            counts += np.histogram(values, bins=edges)[0]
            inspected += len(values)
    if inspected != expected_rows or int(counts.sum()) != expected_rows:
        raise ValueError("Histogram score count differs from scoring artifact")
    return {
        "method": "equal_width_exact_v1",
        "bin_edges": edges,
        "bin_counts": counts.tolist(),
        "score_count": inspected,
        "constant_scores": low == high,
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
    contract = require_output_contract(model)
    features = model["features"]
    label = spec.get("label_column")
    identity_column = spec.get("observation_id_column")
    if label is not None and label in features:
        raise ValueError("Label column must not be a training feature")
    if identity_column is not None and (
        not isinstance(identity_column, str)
        or not identity_column
        or identity_column in features
        or identity_column == label
    ):
        raise ValueError("Observation identity column must be separate from ML features and label")
    temporal = spec.get("temporal_scope")
    selected_time = spec.get("observation_time_column")
    timestamp_column = temporal["timestamp_column"] if temporal else selected_time
    if selected_time is not None and (
        not isinstance(selected_time, str)
        or not selected_time
        or selected_time in features
        or selected_time == label
        or selected_time == identity_column
        or (temporal is not None and selected_time != temporal["timestamp_column"])
    ):
        raise ValueError("Observation time must be separate from ML inputs and match temporal scope")
    fields = [
        *features,
        *([label] if label else []),
        *([identity_column] if identity_column else []),
        *([timestamp_column] if timestamp_column and timestamp_column not in features else []),
    ]
    scope_evidence = None
    pending.parent.mkdir(parents=True, exist_ok=True)
    # 중간 파일의 수명은 Job Attempt에 한정한다. 데이터 전체는 메모리에 올리지 않는다.
    with TemporaryDirectory(prefix=scoring_stage_prefix(pending), dir=pending.parent) as directory:
        staged = Path(directory) / "selected.parquet"
        with pinned_scan_dataset(store, dataset) as source:
            if spec.get("temporal_scope") is not None:
                source, scope_evidence = _scoring_scope(
                    source, dataset, model, spec["temporal_scope"]
                )
            elif selected_time is not None:
                # 시간 범위를 자르지 않고, 관측 시각만 검증·UTC 정규화해 보존한다.
                source = source.with_columns(
                    utc_observation_time(source, selected_time).alias(selected_time)
                )
                invalid = source.select(pl.col(selected_time).is_null().sum()).collect().item()
                if invalid:
                    raise ValueError("Observation time contains missing, unzoned or malformed timestamps")
            schema = source.collect_schema()
            issues = model_input_issues(
                features, model["runtime"].get("feature_dtypes"),
                {name: str(dtype) for name, dtype in schema.items()},
            )
            if issues:
                raise ValueError(" · ".join(issues))
            if any(name not in schema for name in fields):
                raise ValueError("Scoring label, identity or time column is missing")
            source.select(fields).sink_parquet(staged)

        identity_evidence = (
            validate_observation_identity(
                pl.scan_parquet(staged), column=identity_column,
            )
            if identity_column is not None else None
        )
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
                            maximum, minimum = series.max(), series.min()
                            if (
                                (maximum is not None and maximum > 2**53)
                                or (minimum is not None and minimum < -(2**53))
                            ):
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
                    if identity_column is not None:
                        result = result.with_columns(
                            frame[identity_column].alias("observation_id")
                        )
                    if timestamp_column is not None:
                        # 검증된 원본 관측 시각을 Scoring 결과의 동일 행·배치에 고정한다.
                        timestamp = frame[timestamp_column]
                        if isinstance(schema[timestamp_column], pl.Datetime):
                            timestamp = timestamp.dt.strftime("%+")
                        result = result.with_columns(timestamp.alias("observed_at"))
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
        score_summary = _score_summary(score_sums, count, low, high, contract.score_direction)
        score_summary["histogram"] = histogram_from_scoring_file(
            pending, low=low, high=high, expected_rows=count,
        )
        score_summary["scope"] = "scoring_dataset"
        score_summary["score_scale"] = contract.score_scale
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
        if identity_evidence is not None:
            score_summary["observation_identity"] = {
                **identity_evidence,
                "dataset_id": dataset["id"],
                "dataset_version": dataset["version"],
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
            "score_direction": contract.score_direction,
            "score_summary": score_summary,
            "ranking_metrics": ranked_metrics,
            "label_column": label,
        }
