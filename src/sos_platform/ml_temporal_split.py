"""학습 입력의 시각 검증과 Train/Calibration/Test 분할을 수행한다.

각 경계의 시각은 UTC로 비교하고 동일 시각의 행을 다른 구간으로 나누지 않는다.
이 모듈은 데이터 판본·실행·보정 정책을 게시하지 않는다.
"""

from datetime import datetime, timezone
from typing import Literal

import polars as pl


def select_temporal_partition(
    source: pl.LazyFrame,
    *,
    timestamp_column: str,
    train_end: datetime,
    calibration_end: datetime,
    partition: Literal["train", "calibration", "test"],
) -> tuple[pl.LazyFrame, dict]:
    """동일한 분할 정의로 원하는 구간을 선택하고 전체 분할 근거를 반환한다.

    Args:
        source: 고정된 Dataset의 원본 LazyFrame.
        timestamp_column: UTC 오프셋을 가진 RFC3339 텍스트 또는 timezone-aware Datetime.
        train_end: Calibration 시작 시각(Train은 이 시각 미만).
        calibration_end: Test 시작 시각(Calibration은 이 시각 미만).

    Returns:
        선택 구간 필터 LazyFrame과 전체/각 구간의 정확한 행 수를 포함한 근거.

    Raises:
        ValueError: 시각 형식이 불명확하거나 null·파싱 불가능 값이 있는 경우.
    """
    if (
        train_end.tzinfo is None
        or calibration_end.tzinfo is None
        or not train_end < calibration_end
    ):
        raise ValueError("Temporal split boundaries must be ordered timezone-aware datetimes")
    schema = source.collect_schema()
    if timestamp_column not in schema:
        raise ValueError("Temporal split timestamp column is missing")
    if "__sos_event_time" in schema:
        raise ValueError("Dataset contains reserved temporal column")
    dtype = schema[timestamp_column]
    if dtype == pl.String:
        # 문자열을 암묵적으로 로컬 시각으로 해석하지 않는다.
        values = pl.col(timestamp_column)
        # 일부 실제 Parquet 소스는 UTC 오프셋을 +00처럼 시간 단위로 기록한다.
        # 오프셋 없는 문자열과 구별하면서 +HH, +HHMM, +HH:MM을 같은 의미로 정규화한다.
        explicit_timezone = values.str.contains(r"(?:Z|[+-]\d{2}(?::?\d{2})?)$")
        normalized = (
            values.str.replace(r"([+-]\d{2})$", "${1}:00")
            .str.replace(r"([+-]\d{2})(\d{2})$", "${1}:${2}")
            .str.replace(r"Z$", "+00:00")
        )
        parsed = (
            pl.when(explicit_timezone)
            .then(normalized.str.to_datetime(time_zone="UTC", strict=False))
            .otherwise(pl.lit(None, dtype=pl.Datetime(time_zone="UTC")))
        )
    elif isinstance(dtype, pl.Datetime) and dtype.time_zone:
        parsed = pl.col(timestamp_column).dt.convert_time_zone("UTC")
    else:
        raise ValueError("Temporal split requires timezone-aware event timestamps")

    prepared = source.with_columns(parsed.alias("__sos_event_time"))
    start_calibration = train_end.astimezone(timezone.utc)
    start_test = calibration_end.astimezone(timezone.utc)
    event = pl.col("__sos_event_time")
    summary = prepared.select(
        pl.len().alias("input_total_rows"),
        event.is_null().sum().alias("invalid_timestamp_rows"),
        (event < start_calibration).sum().alias("train_rows"),
        ((event >= start_calibration) & (event < start_test)).sum().alias("calibration_rows"),
        (event >= start_test).sum().alias("test_rows"),
    ).collect().row(0, named=True)
    if summary["invalid_timestamp_rows"]:
        raise ValueError("Temporal split contains missing, unzoned or malformed timestamps")
    if min(summary["train_rows"], summary["calibration_rows"], summary["test_rows"]) < 1:
        raise ValueError("Temporal split requires nonempty Train, Calibration and Test partitions")
    if partition == "train":
        selected = event < start_calibration
    elif partition == "calibration":
        selected = (event >= start_calibration) & (event < start_test)
    elif partition == "test":
        selected = event >= start_test
    else:
        raise ValueError("Unknown temporal partition")
    frame = prepared.filter(selected).drop("__sos_event_time")
    evidence = {
        "timestamp_column": timestamp_column,
        "train_end": start_calibration.isoformat(),
        "calibration_end": start_test.isoformat(),
        **{key: int(summary[key]) for key in (
            "input_total_rows", "train_rows", "calibration_rows", "test_rows"
        )},
    }
    evidence["partition"] = partition
    return frame, evidence


def split_training_source(
    source: pl.LazyFrame,
    *,
    timestamp_column: str,
    train_end: datetime,
    calibration_end: datetime,
) -> tuple[pl.LazyFrame, dict]:
    """기존 Train-only 실행 계약을 유지하는 시간 구간 선택 진입점."""
    return select_temporal_partition(
        source,
        timestamp_column=timestamp_column,
        train_end=train_end,
        calibration_end=calibration_end,
        partition="train",
    )
