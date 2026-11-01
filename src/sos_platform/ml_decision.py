"""불변 Scoring Result에 여러 정책·평가를 독립 적용한다."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from .duckdb_spill import SpillBudget
from .ml_policy_contract import validate_policy_for_scoring
from .object_store import ObjectStore
from .result_staging import result_stage, result_stage_bytes
from .score_files import quantile_threshold_file, summarize_scores


@contextmanager
def _verified_scoring(store, scoring: dict, stage_owner: Path) -> Iterator[tuple[Path, SpillBudget]]:
    """해시를 검증한 Scoring Parquet과 이 Attempt Stage의 Spill 예산을 제공한다.

    행 수·필수 컬럼·행 순서는 배치로 순회하며 확인한다. 점수를 메모리에 모으지 않으며, Policy·Evaluation은
    파일 계산(`score_files`)으로 같은 결과를 만든다. 내려받은 결과와 외부 정렬 Spill은 Attempt Stage에
    두어 Worker의 Stage 한도 검사와 회수 대상이 된다.
    """
    storage = ObjectStore()
    expected = f"results/{scoring['job_id']}/{scoring['attempt_id']}.scores.parquet"
    if scoring["location"] != storage.location(expected):
        raise ValueError("Scoring artifact is outside its immutable Attempt location")
    with result_stage(stage_owner) as directory:
        path = directory / "scores.parquet"
        storage.get(expected, path, expected_sha256=scoring["sha256"])
        with pq.ParquetFile(path) as parquet:
            if parquet.metadata.num_rows != scoring["row_count"]:
                raise ValueError("Scoring artifact row count does not match the revision")
            if not {"row_index", "anomaly_score"}.issubset(parquet.schema_arrow.names):
                raise ValueError("Invalid Scoring artifact columns")
            position = 0
            for batch in parquet.iter_batches(batch_size=65_536, columns=["row_index"]):
                indices = batch.column(0).to_numpy(zero_copy_only=False)
                if not np.array_equal(indices, np.arange(position, position + len(indices))):
                    raise ValueError("Scoring artifact row order does not match its revision")
                position += len(indices)
        yield path, SpillBudget.for_worker(
            directory, stage_error="SOS-ML-SCORE-STAGE-LIMIT",
            capacity_error="SOS-ML-SCORE-CAPACITY",
            used_bytes=lambda: result_stage_bytes(stage_owner),
        )


def _labeled(path: Path) -> bool:
    with pq.ParquetFile(path) as parquet:
        return "label" in parquet.schema_arrow.names


def _verified_scoring_row(store, scoring: dict, row_index: int, stage_owner: Path) -> dict:
    """해시 검증된 Scoring Parquet에서 설명 대상 Row Group만 불러온다.

    Args:
        store: 조회에 사용할 SOS Store와 Attempt별 캐시.
        scoring: 원본 Scoring Result의 불변 위치·SHA-256·행 수.
        row_index: 결과 Parquet에서 생성한 전역 순번.
        stage_owner: 내려받은 결과를 둘 Attempt Stage의 소유 경로.

    Returns:
        dict: 선택한 한 행의 점수, 원본 Feature struct, 선택적 관측치 UUID.

    Raises:
        ValueError: 범위, SHA, 경로, 아티팩트 행 수 또는 순서 위반.
    """
    if type(row_index) is not int or not 0 <= row_index < scoring["row_count"]:
        raise ValueError("Explanation row_index is out of bounds")
    storage = ObjectStore()
    expected = f"results/{scoring['job_id']}/{scoring['attempt_id']}.scores.parquet"
    if scoring["location"] != storage.location(expected):
        raise ValueError("Scoring artifact is outside its immutable Attempt location")
    with result_stage(stage_owner) as directory:
        path = directory / "scores.parquet"
        storage.get(expected, path, expected_sha256=scoring["sha256"])
        with pq.ParquetFile(path) as parquet:
            if parquet.metadata.num_rows != scoring["row_count"]:
                raise ValueError("Scoring artifact row count does not match the revision")
            required = {"row_index", "anomaly_score", "features"}
            if not required.issubset(parquet.schema_arrow.names):
                raise ValueError("Invalid Scoring artifact columns")
            offset = 0
            for group in range(parquet.num_row_groups):
                length = parquet.metadata.row_group(group).num_rows
                if offset <= row_index < offset + length:
                    selected_columns = sorted(
                        required | (
                            {"observation_id"}
                            if "observation_id" in parquet.schema_arrow.names else set()
                        )
                    )
                    frame = pl.from_arrow(
                        parquet.read_row_group(group, columns=selected_columns)
                    )
                    row = frame.row(row_index - offset, named=True)
                    if row["row_index"] != row_index:
                        raise ValueError("Scoring artifact row order does not match its revision")
                    return row
                offset += length
    raise ValueError("Explanation row_index is out of bounds")


def apply_scoring_policy(store, spec: dict, stage_owner: Path) -> dict:
    """고정 또는 참조 분위수 임계값을 기존 점수에만 적용한다."""
    source = store.get_scoring_result(spec["scoring_result_id"])
    if source is None or source["owner_actor"] != spec["source_owner_actor"]:
        raise ValueError("Scoring Result is unavailable")
    mode = spec["mode"]
    if mode not in ("fixed", "quantile"):
        raise ValueError("Unsupported Decision Policy")
    with _verified_scoring(store, source, stage_owner) as (path, budget):
        threshold = (
            spec["threshold"] if mode == "fixed"
            else quantile_threshold_file(path, spec["alert_fraction"], budget)
        )
        # 임계값 탐색은 정확도가 아니다. 정책 선택과 독립 평가셋 검증을 구별한다.
        result = summarize_scores(path, threshold=threshold)
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


def _verified_temporal_nonoverlap(reference: dict, target: dict) -> bool:
    """동일 Dataset 판본의 고정 UTC 분할에서 보정·평가 구간이 서로 분리되는지 확인한다.

    이 결과는 관측치의 시간 구간 비중복이지 시계열 표본의 통계적 독립성이 아니다.
    """
    a = reference.get("score_summary", {}).get("temporal_scope")
    b = target.get("score_summary", {}).get("temporal_scope")
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    pinned = (
        "dataset_id", "dataset_version", "model_revision_id",
        "timestamp_column", "train_end", "calibration_end",
    )
    return (
        a.get("partition") == "calibration"
        and b.get("partition") == "test"
        and all(a.get(name) is not None and a.get(name) == b.get(name) for name in pinned)
        and reference.get("dataset_id") == target.get("dataset_id") == a["dataset_id"]
        and reference.get("dataset_version") == target.get("dataset_version")
        == a["dataset_version"]
        and reference.get("model_revision_id") == target.get("model_revision_id")
        == a["model_revision_id"]
    )


def evaluate_scoring_policy(store, spec: dict, stage_owner: Path) -> dict:
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
    threshold = policy["threshold"] if policy else None
    with _verified_scoring(store, source, stage_owner) as (path, _):
        labeled = _labeled(path)
        if labeled and threshold is None:
            # 랭킹 지표는 정책 없이 측정할 수 있지만 이진 판정 지표는 정의하지 않는다.
            summary = summarize_scores(path)
            summary["evaluation_kind"] = "ranking_only"
        else:
            summary = summarize_scores(path, threshold=threshold, labeled=labeled)
    if reference is not None:
        summary["policy_reference_scoring_result_id"] = reference["id"]
        temporal_nonoverlap = _verified_temporal_nonoverlap(reference, source)
        summary["policy_reference_relation"] = (
            "same_scoring_result" if reference["id"] == source["id"]
            else "distinct_calibration_test_time_ranges" if temporal_nonoverlap
            else "different_scoring_result_disjointness_unverified"
        )
        summary["temporal_nonoverlap_confirmed"] = temporal_nonoverlap
        # 시간 구간 비중복은 통계적 독립성이나 공격 탐지 성능의 증명이 아니다.
        summary["independent_validation_confirmed"] = False
    return {
        "scoring_result_id": source["id"],
        "policy_revision_id": policy_id,
        "label_coverage": "full" if labeled else "none",
        "decision_metrics": summary,
        "ranking_metrics": source["ranking_metrics"],
    }
