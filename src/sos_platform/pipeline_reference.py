"""Pipeline 제작자가 제공하는 첫 IF Pipeline 정의와 Dataset 적합성 검사.

이 정의의 Feature 계약은 SOS Core의 공통 입력 규칙이 아니다.
"""
from datetime import datetime, timedelta, timezone

import polars as pl

from .datasets import pinned_scan_dataset
from .ml_input import NUMERIC
from .ml_observation_identity import validate_observation_identity
from .ml_temporal_split import select_temporal_partition

PIPELINE_ID = "activity-anomaly-if"
PIPELINE_VERSION = "1"
FEATURES = ("io_bytes_per_sec", "net_bytes_per_sec", "cnt_file_create", "cnt_file_del", "cnt_file_write", "cnt_file_rename", "cnt_proc_exec", "cnt_net_tcp", "cnt_net_udp", "cnt_large_write", "cnt_rename_ext_change", "cnt_proc_completed_in_batch", "max_write_size", "ratio_offhour_activity", "delta_file_vol", "velocity_net_conn", "velocity_proc_exec", "delta_avg_filename_entropy", "avg_write_chunk_size", "cv_write_chunk_size", "uniq_browser_pages", "uniq_file_touched", "uniq_new_ext_on_rename", "uniq_proc_image", "uniq_dest_port", "uniq_dest_ip", "ratio_private_ip", "uniq_file_ext", "uniq_dir_touched", "avg_filename_entropy", "max_cmd_len", "max_url_len", "avg_file_path_depth", "avg_net_payload_out", "ratio_no_ui", "ratio_hidden_file_ops", "read_write_bytes_ratio", "ratio_rename_burst", "ratio_large_write", "ratio_ext_change_on_rename", "ratio_proc_churn", "ratio_top1_dest_bytes_out", "ratio_outbound_traffic", "avg_ops_per_file")
STAGES = ("preflight", "training", "calibration", "policy", "test", "evaluation")


def definition() -> dict:
    """실제 지원 범위를 과장하지 않는 공개 Pipeline Catalog 항목."""
    return {
        "id": PIPELINE_ID,
        "version": PIPELINE_VERSION,
        "name": "활동 이상 탐지",
        "learning_type": "unsupervised",
        "task_type": "anomaly_detection",
        "topic": "activity_anomaly",
        "description": "호환되는 사용자·호스트 활동 Feature 데이터로 새 Isolation Forest 모델을 학습하고, "
                       "Calibration 정책을 별도 Test 구간에 적용합니다.",
        "algorithm": "isolation_forest",
        "required_time_column": "ts",
        "required_features": list(FEATURES),
        "stages": list(STAGES),
        "model_reuse": False,
        "label_requirement": "none",
        "limitations": [
            "등록된 Feature 이름과 숫자형 자료형이 일치하는 Dataset만 지원합니다.",
            "시간 컬럼 ts는 명시적 UTC 오프셋이 있는 RFC3339 텍스트 또는 timezone-aware Timestamp여야 합니다.",
            "이상 후보는 실제 이상 사건이나 탐지 정확도의 증명이 아닙니다.",
            "sos_observation_id가 있으면 고정 Dataset 내 유효한 UUIDv4를 요구합니다. 시각은 고유 ID가 아닙니다.",
            "모델은 실행한 사용자의 Dataset으로 학습합니다. 다른 사용자에게는 소유자가 명시적으로 제공한 모델 판본만 보이며 학습 데이터는 공유하지 않습니다.",
            "임계값은 SOS의 선형 보간 분위수와 경계 포함 판정(>=)을 사용하며, 외부 참조 스크립트와 후보 수가 다를 수 있습니다.",
        ],
    }


def _event_time(source: pl.LazyFrame) -> pl.Expr:
    """기존 시간 분할과 같은 명시적 오프셋 파싱 계약."""
    dtype = source.collect_schema()["ts"]
    if isinstance(dtype, pl.Datetime) and dtype.time_zone:
        return pl.col("ts").dt.convert_time_zone("UTC")
    if dtype != pl.String:
        raise ValueError("ts는 UTC 오프셋 문자열 또는 timezone-aware Datetime이어야 합니다.")
    values = pl.col("ts")
    marked = values.str.contains(r"(?:Z|[+-]\d{2}(?::?\d{2})?)$")
    normalized = (
        values.str.replace(r"([+-]\d{2})$", "${1}:00")
        .str.replace(r"([+-]\d{2})(\d{2})$", "${1}:${2}")
        .str.replace(r"Z$", "+00:00")
    )
    return pl.when(marked).then(
        normalized.str.to_datetime(time_zone="UTC", strict=False)
    ).otherwise(pl.lit(None, dtype=pl.Datetime(time_zone="UTC")))


def inspect_dataset(store, dataset: dict, *, full: bool = False) -> dict:
    """기본 스키마 검사와 자원 격리된 Worker 내 전체 시간 검사를 구별한다.

    full=True 검사는 반드시 Worker의 자원 예산 안에서만 호출한다.
    """
    issues: list[str] = []
    plan = None
    with pinned_scan_dataset(store, dataset) as source:
        schema = source.collect_schema()
        missing = [name for name in ("ts", *FEATURES) if name not in schema]
        if missing:
            issues.append("필수 컬럼 없음: " + ", ".join(missing[:12])
                          + (" 외 " + str(len(missing)-12) + "개" if len(missing)>12 else ""))
        bad_types = [name for name in FEATURES if name in schema and schema[name] not in NUMERIC]
        if bad_types:
            issues.append("숫자형이 아닌 Feature: " + ", ".join(bad_types[:12]))
        if "ts" in schema and schema["ts"] != pl.String and not (
            isinstance(schema["ts"], pl.Datetime) and schema["ts"].time_zone
        ):
            issues.append("ts는 명시적 시간대가 있는 문자열 또는 Datetime이어야 합니다.")
        identity_column = "sos_observation_id" if "sos_observation_id" in schema else None
        if identity_column and schema[identity_column] != pl.String:
            issues.append("sos_observation_id는 UUIDv4 문자열이어야 합니다.")
        if not issues and full:
            try:
                if identity_column:
                    validate_observation_identity(source, column=identity_column)
                event = _event_time(source)
                milliseconds = event.dt.epoch("ms")
                stats = source.select(
                    pl.len().alias("count"),
                    event.is_null().sum().alias("invalid"),
                    milliseconds.quantile(0.6, interpolation="nearest").alias("train"),
                    milliseconds.quantile(0.8, interpolation="nearest").alias("calibration"),
                ).collect().row(0, named=True)
                if stats["count"] < 3 or stats["invalid"]:
                    issues.append("시간 값에 결측·오프셋 누락·잘못된 형식이 있거나 행 수가 부족합니다.")
                else:
                    train_end = datetime.fromtimestamp(stats["train"] / 1000, tz=timezone.utc) + timedelta(milliseconds=1)
                    calibration_end = datetime.fromtimestamp(stats["calibration"] / 1000, tz=timezone.utc) + timedelta(milliseconds=1)
                    _, evidence = select_temporal_partition(
                        source, timestamp_column="ts",
                        train_end=train_end, calibration_end=calibration_end, partition="train",
                    )
                    plan = {
                        "timestamp_column": "ts",
                        "observation_id_column": identity_column,
                        "train_end": train_end.isoformat(),
                        "calibration_end": calibration_end.isoformat(),
                        "partition_sizes": {
                            "train": evidence["train_rows"],
                            "calibration": evidence["calibration_rows"],
                            "test": evidence["test_rows"],
                        },
                    }
            except (ValueError, pl.exceptions.PolarsError, OverflowError) as exc:
                issues.append(
                    "관측치 ID가 유효하지 않습니다. 중복·형식·결측값을 확인하세요."
                    if identity_column and "Observation identity" in str(exc)
                    else "시간 분할이 불가능합니다. ts의 UTC 오프셋·유효값·기간별 행을 확인하세요."
                )
    return {
        "pipeline_id": PIPELINE_ID, "pipeline_version": PIPELINE_VERSION,
        "dataset_id": dataset["id"], "dataset_version": dataset["version"],
        "ready": not issues, "issues": issues, "feature_count": len(FEATURES), "plan": plan,
    }
