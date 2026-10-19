"""불변 Scoring Result에 여러 정책·평가를 독립 적용한다."""

from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from .object_store import ObjectStore


def _verified_scoring(store, scoring: dict) -> pl.DataFrame:
    """Scoring Result의 공개된 위치와 SHA-256을 검증하여 행별 점수를 읽는다."""
    storage = ObjectStore()
    expected = f"results/{scoring['job_id']}/{scoring['attempt_id']}.scores.parquet"
    if scoring["location"] != storage.location(expected):
        raise ValueError("Scoring artifact is outside its immutable Attempt location")
    with TemporaryDirectory(prefix="ml-scoring-", dir=store.home / "cache") as directory:
        path = Path(directory) / "scores.parquet"
        storage.get(expected, path, expected_sha256=scoring["sha256"])
        frame = pl.read_parquet(path)
    if frame.height != scoring["row_count"]:
        raise ValueError("Scoring artifact row count does not match the revision")
    if "anomaly_score" not in frame.columns or "row_index" not in frame.columns:
        raise ValueError("Invalid Scoring artifact columns")
    if frame["row_index"].to_list() != list(range(frame.height)):
        raise ValueError("Scoring artifact row order does not match its revision")
    return frame


def apply_scoring_policy(store, spec: dict) -> dict:
    """고정 또는 참조 분위수 임계값을 기존 점수에만 적용한다."""
    source = store.get_scoring_result(spec["scoring_result_id"])
    if source is None or source["owner_actor"] != spec["source_owner_actor"]:
        raise ValueError("Scoring Result is unavailable")
    frame = _verified_scoring(store, source)
    scores = frame["anomaly_score"].to_list()
    mode = spec["mode"]
    if mode == "fixed":
        threshold = spec["threshold"]
    elif mode == "quantile":
        threshold = quantile_threshold(scores, spec["alert_fraction"])
    else:
        raise ValueError("Unsupported Decision Policy")
    # 임계값 탐색은 정확도가 아니다. 정책 선택과 독립 평가셋 검증을 구별한다.
    result = evaluate_anomaly_scores(scores, threshold=threshold)
    result["evaluation_scope"] = "scoring_dataset_without_ground_truth"
    return {
        "scoring_result_id": source["id"],
        "mode": mode,
        "threshold": threshold,
        "alert_fraction": spec.get("alert_fraction"),
        "decision_summary": result,
    }


def evaluate_scoring_policy(store, spec: dict) -> dict:
    """기존 점수와 선택한 정책으로 감독·비지도 평가 근거를 남긴다."""
    source = store.get_scoring_result(spec["scoring_result_id"])
    if source is None or source["owner_actor"] != spec["source_owner_actor"]:
        raise ValueError("Scoring Result is unavailable")
    policy_id = spec.get("policy_revision_id")
    policy = store.get_decision_policy_revision(policy_id) if policy_id else None
    if policy_id and (policy is None or policy["scoring_result_id"] != source["id"]):
        raise ValueError("Decision Policy does not belong to this Scoring Result")
    frame = _verified_scoring(store, source)
    label_values = frame["label"].to_list() if "label" in frame.columns else None
    threshold = policy["threshold"] if policy else None
    if label_values is not None and threshold is None:
        # 랭킹 지표는 정책 없이 측정할 수 있지만 이진 판정 지표는 정의하지 않는다.
        summary = evaluate_anomaly_scores(frame["anomaly_score"].to_list())
    else:
        summary = evaluate_anomaly_scores(
            frame["anomaly_score"].to_list(),
            threshold=threshold,
            labels=label_values,
        )
    return {
        "scoring_result_id": source["id"],
        "policy_revision_id": policy_id,
        "label_coverage": "full" if label_values is not None else "none",
        "decision_metrics": summary,
        "ranking_metrics": source["ranking_metrics"],
    }
