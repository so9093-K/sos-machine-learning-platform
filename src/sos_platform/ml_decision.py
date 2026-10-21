"""불변 Scoring Result에 여러 정책·평가를 독립 적용한다."""

from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
import pyarrow.parquet as pq

from .ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from .object_store import ObjectStore


def _verified_scoring(
    store, scoring: dict, *, include_features: bool = False
) -> pl.DataFrame:
    """Scoring 무결성과 행 순서를 검증하며 필요한 컬럼만 메모리에 적재한다.

    Policy/Evaluation은 이상 점수·레이블만 조회한다. 설명은 명시적으로
    include_features=True를 요청해야 원본 Feature 구조를 가져온다.
    """
    storage = ObjectStore()
    expected = f"results/{scoring['job_id']}/{scoring['attempt_id']}.scores.parquet"
    if scoring["location"] != storage.location(expected):
        raise ValueError("Scoring artifact is outside its immutable Attempt location")
    with TemporaryDirectory(prefix="ml-scoring-", dir=store.home / "cache") as directory:
        path = Path(directory) / "scores.parquet"
        storage.get(expected, path, expected_sha256=scoring["sha256"])
        if include_features:
            frame = pl.read_parquet(path)
        else:
            with pq.ParquetFile(path) as parquet:
                columns = ["row_index", "anomaly_score"]
                if "label" in parquet.schema_arrow.names:
                    columns.append("label")
            frame = pl.read_parquet(path, columns=columns)
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
    result["threshold_reference"] = (
        "current_scoring_result" if mode == "quantile" else "explicit_fixed"
    )
    return {
        "scoring_result_id": source["id"],
        "mode": mode,
        "threshold": threshold,
        "alert_fraction": spec.get("alert_fraction"),
        "decision_summary": result,
    }


def validate_policy_for_scoring(
    store,
    policy: dict,
    target: dict,
    *,
    expected_policy_owner: str | None = None,
    expected_reference_owner: str | None = None,
) -> dict:
    """정책 보정 근거와 평가 입력의 모델·점수 의미가 같은지 검사한다.

    정책은 보정 Scoring Result를 영구 참조하며 새 평가 대상에 재사용할 수 있다.
    다른 Scoring Result라고 해서 관측 행이 서로 겹치지 않는다는 보장은 없다.

    Args:
        store: 판본 조회를 제공하는 SOS Store.
        policy: 사용 권한을 확인한 불변 Policy Revision.
        target: 평가 대상 Scoring Result.
        expected_policy_owner: Job 제출 시 고정한 정책 소유자.
        expected_reference_owner: Job 제출 시 고정한 보정 Scoring 소유자.

    Returns:
        dict: 보정 근거인 Scoring Result.

    Raises:
        ValueError: 보정 근거·소유자·Model Revision·점수 계약 불일치.
    """
    if expected_policy_owner is not None and policy["owner_actor"] != expected_policy_owner:
        raise ValueError("Decision Policy owner changed")
    reference = store.get_scoring_result(policy["scoring_result_id"])
    if reference is None:
        raise ValueError("Decision Policy calibration source is unavailable")
    if expected_reference_owner is not None and reference["owner_actor"] != expected_reference_owner:
        raise ValueError("Decision Policy calibration owner changed")
    if reference["model_revision_id"] != target["model_revision_id"]:
        raise ValueError("Decision Policy requires the same Model Revision")
    if reference["score_direction"] != target["score_direction"]:
        raise ValueError("Decision Policy score direction differs from target")
    if reference["feature_names"] != target["feature_names"]:
        raise ValueError("Decision Policy feature signature differs from target")
    if reference["score_summary"].get("score_scale") != target["score_summary"].get("score_scale"):
        raise ValueError("Decision Policy score scale differs from target")
    return reference


def evaluate_scoring_policy(store, spec: dict) -> dict:
    """정책 산정 Scoring과 적용 Scoring을 구분해 평가 근거를 남긴다."""
    source = store.get_scoring_result(spec["scoring_result_id"])
    if source is None or source["owner_actor"] != spec["source_owner_actor"]:
        raise ValueError("Scoring Result is unavailable")
    policy_id = spec.get("policy_revision_id")
    policy = store.get_decision_policy_revision(policy_id) if policy_id else None
    if policy_id and policy is None:
        raise ValueError("Decision Policy is unavailable")
    reference = (
        validate_policy_for_scoring(
            store, policy, source,
            expected_policy_owner=spec.get("policy_owner_actor"),
            expected_reference_owner=spec.get("reference_owner_actor"),
        )
        if policy is not None else None
    )
    frame = _verified_scoring(store, source)
    label_values = frame["label"].to_list() if "label" in frame.columns else None
    threshold = policy["threshold"] if policy else None
    if label_values is not None and threshold is None:
        # 랭킹 지표는 정책 없이 측정할 수 있지만 이진 판정 지표는 정의하지 않는다.
        summary = evaluate_anomaly_scores(frame["anomaly_score"].to_list())
        summary["evaluation_kind"] = "ranking_only"
    else:
        summary = evaluate_anomaly_scores(
            frame["anomaly_score"].to_list(),
            threshold=threshold,
            labels=label_values,
        )
    if reference is not None:
        summary["policy_reference_scoring_result_id"] = reference["id"]
        summary["policy_reference_relation"] = (
            "same_scoring_result" if reference["id"] == source["id"]
            else "different_scoring_result_disjointness_unverified"
        )
        # 판본 ID가 다른 것만으로 Calibration/Test의 독립성을 주장하지 않는다.
        summary["independent_validation_confirmed"] = False
    return {
        "scoring_result_id": source["id"],
        "policy_revision_id": policy_id,
        "label_coverage": "full" if label_values is not None else "none",
        "decision_metrics": summary,
        "ranking_metrics": source["ranking_metrics"],
    }
