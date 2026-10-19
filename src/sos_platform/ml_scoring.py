"""기존 Model Revision을 다시 학습하지 않고 독립 Dataset에 적용한다."""

from pathlib import Path

import numpy as np
import polars as pl
from sklearn.metrics import average_precision_score, roc_auc_score

from .datasets import pinned_scan_dataset
from .ml_anomaly import evaluate_anomaly_scores
from .ml_isolation_forest import _matrix
from .ml_runtime import load_trusted_isolation_forest
from .ml_training import INTEGER_DTYPES, MAX_ROWS, NUMERIC


def run_dataset_scoring(store, dataset: dict, spec: dict, pending: Path) -> dict:
    """신뢰된 모델로 입력 행별 점수를 계산해 별도 불변 Parquet 결과를 만든다.

    Scoring Result의 row_index는 결과 파일 내 행 번호이며, Dataset의
    전역 ID나 다른 스캔과 연결 가능한 원본 행 식별자를 뜻하지 않는다.
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
    with pinned_scan_dataset(store, dataset) as source:
        schema = source.collect_schema()
        if any(name not in schema for name in fields):
            raise ValueError("Scoring feature or label is missing")
        if any(schema[name] not in NUMERIC for name in features):
            raise ValueError("Scoring features must be finite numeric values")
        if [str(schema[name]) for name in features] != model["runtime"].get("feature_dtypes"):
            raise ValueError("Scoring feature dtypes differ from Model Revision")
        frame = source.select(fields).head(MAX_ROWS + 1).collect()
    if not 1 <= frame.height <= MAX_ROWS:
        raise ValueError("Scoring requires between 1 and 50000 rows")
    if any(frame.select(features).null_count().row(0)):
        raise ValueError("Scoring features contain null values")
    safe_integer = 2**53
    for name in features:
        if schema[name] in INTEGER_DTYPES:
            field = frame[name]
            if field.max() > safe_integer or field.min() < -safe_integer:
                raise ValueError("Integer feature exceeds the exact float64 range")
    matrix = _matrix(frame.select(features).to_numpy(), n_features=len(features))
    model_adapter = load_trusted_isolation_forest(model, store.home / "cache")
    scores = model_adapter.score_anomalies(matrix)
    score_summary = evaluate_anomaly_scores(scores)
    score_summary["scope"] = "independent_scoring_dataset"
    score_summary["score_scale"] = "negative_sklearn_score_samples"
    ranked_metrics = None
    labels = None
    if label is not None:
        if frame[label].null_count() != 0:
            raise ValueError("Labeled scoring requires complete binary labels")
        labels = frame[label].to_list()
        if any(type(value) not in (int, float) or value not in (0, 1) for value in labels):
            raise ValueError("Scoring labels must be binary 0/1")
        labels = [int(value) for value in labels]
        ranked_metrics = {
            "scope": "fully_labeled_scoring_dataset",
            "label_column": label,
            "positive_count": sum(labels),
            "negative_count": len(labels) - sum(labels),
            "average_precision": None,
            "roc_auc": None,
        }
        if 0 < sum(labels) < len(labels):
            ranked_metrics["average_precision"] = float(average_precision_score(labels, scores))
            ranked_metrics["roc_auc"] = float(roc_auc_score(labels, scores))
    result = pl.DataFrame({
        "row_index": list(range(frame.height)),
        "anomaly_score": scores,
    }).with_columns(frame.select(features).to_struct("features"))
    if labels is not None:
        result = result.with_columns(pl.Series(name="label", values=labels, dtype=pl.Int8))
    result.write_parquet(pending)
    return {
        "model_revision_id": model["id"],
        "dataset_version": dataset["version"],
        "feature_names": features,
        "row_count": frame.height,
        "score_direction": "higher_is_more_anomalous",
        "score_summary": score_summary,
        "ranking_metrics": ranked_metrics,
        "label_column": label,
    }
