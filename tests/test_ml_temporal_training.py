"""IF 학습의 독립 시간 분할·중앙값 결측 정책·API 근거를 검증한다."""

from contextlib import contextmanager
from datetime import datetime, timezone

import joblib
import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform import ml_training
from sos_platform.schemas import MLTemporalSplitSpec, MLTrainRequest, MLTrainingInputPolicy
from sos_platform.ml_temporal_split import split_training_source


def bind_frame(monkeypatch, frame: pl.DataFrame) -> None:
    @contextmanager
    def scan(_store, _dataset):
        yield frame.lazy()

    monkeypatch.setattr(ml_training, "pinned_scan_dataset", scan)


def temporal_config() -> MLTemporalSplitSpec:
    return MLTemporalSplitSpec(
        timestamp_column="ts",
        train_end="2026-01-01T04:00:00Z",
        calibration_end="2026-01-01T07:00:00Z",
    )


def events() -> pl.DataFrame:
    return pl.DataFrame({
        "ts": [f"2026-01-01T{hour:02d}:00:00Z" for hour in range(9)],
        "value": [1.0, None, 3.0, 9.0, 1000.0, None, 9999.0, 1e6, 1e7],
    })


def test_time_split_excludes_future_rows_and_records_exact_partition_sizes(monkeypatch):
    bind_frame(monkeypatch, events())
    frame, evidence = ml_training.training_matrix(
        None, {"version": "d1"}, ["value"],
        input_policy=MLTrainingInputPolicy(mode="all"),
        temporal_split=temporal_config(), missing_policy="train_median",
        worker_memory_mib=64,
    )
    assert frame.shape == (4, 1)
    assert np.isnan(frame).sum() == 1
    assert np.nanmax(frame) == 9.0
    split = evidence["temporal_split"]
    assert split["input_total_rows"] == 9
    assert (split["train_rows"], split["calibration_rows"], split["test_rows"]) == (4, 3, 2)
    assert evidence["source_rows"] == 4
    assert split["train_end"] == "2026-01-01T04:00:00+00:00"


def test_fitted_model_does_not_use_calibration_or_test_to_impute(monkeypatch, tmp_path):
    bind_frame(monkeypatch, events())
    model_path = tmp_path / "model.joblib"
    result = ml_training.train_dataset(
        None, {"id": "dataset", "version": "d1"}, {
            "algorithm": "isolation_forest",
            "dataset_version": "d1",
            "features": ["value"],
            "params": {"n_estimators": 8, "random_state": 42},
            "training_input": {"mode": "all"},
            "missing_policy": "train_median",
            "temporal_split": temporal_config().model_dump(mode="json"),
        }, model_path,
    )
    bundle = joblib.load(model_path)
    assert bundle["preprocessing"].medians == (3.0,)
    assert result["training_rows"] == 4
    assert result["runtime"]["training_missing_policy"] == "train_median"
    assert result["training_selection"]["temporal_split"]["calibration_rows"] == 3


def test_identical_timestamp_rows_do_not_cross_cutoff():
    source = pl.DataFrame({
        "ts": ["2026-01-01T03:00:00Z", "2026-01-01T04:00:00Z",
               "2026-01-01T04:00:00Z", "2026-01-01T07:00:00Z"],
        "value": [1, 2, 3, 4],
    }).lazy()
    frame, evidence = split_training_source(
        source, timestamp_column="ts", train_end=temporal_config().train_end,
        calibration_end=temporal_config().calibration_end,
    )
    assert frame.select("value").collect()["value"].to_list() == [1]
    assert (evidence["train_rows"], evidence["calibration_rows"], evidence["test_rows"]) == (
        1, 2, 1
    )


@pytest.mark.parametrize("invalid", [
    "2026-01-01T05:00:00", "not-a-time", None,
])
def test_missing_unzoned_or_invalid_event_time_rejected(invalid):
    values = events().with_columns(
        pl.when(pl.int_range(pl.len()) == 5)
        .then(pl.lit(invalid))
        .otherwise(pl.col("ts"))
        .alias("ts")
    )
    with pytest.raises(ValueError, match="timestamp"):
        split_training_source(
            values.lazy(), timestamp_column="ts", train_end=temporal_config().train_end,
            calibration_end=temporal_config().calibration_end,
        )


def test_naive_timestamp_dtype_is_rejected():
    frame = pl.DataFrame({"ts": [datetime(2026, 1, 1), datetime(2026, 1, 2)]})
    with pytest.raises(ValueError, match="timezone-aware"):
        split_training_source(
            frame.lazy(), timestamp_column="ts", train_end=temporal_config().train_end,
            calibration_end=temporal_config().calibration_end,
        )


@pytest.mark.parametrize("changes", [
    {"train_end": "2026-01-01T04:00:00", "calibration_end": "2026-01-01T07:00:00Z"},
    {"train_end": "2026-01-01T08:00:00Z", "calibration_end": "2026-01-01T07:00:00Z"},
])
def test_api_disallows_naive_or_reversed_boundaries(changes):
    with pytest.raises(ValidationError):
        MLTemporalSplitSpec(timestamp_column="ts", **changes)


def test_temporal_column_cannot_be_model_feature():
    with pytest.raises(ValidationError, match="timestamp"):
        MLTrainRequest(
            dataset_id="data", features=["ts", "value"], temporal_split=temporal_config()
        )


def test_no_time_split_remains_legacy_default_and_rejects_null(monkeypatch):
    bind_frame(monkeypatch, events())
    with pytest.raises(ValueError, match="null"):
        ml_training.training_matrix(
            None, {"version": "d1"}, ["value"],
            input_policy=MLTrainingInputPolicy(mode="all"), worker_memory_mib=64,
        )


def test_explicit_utc_datetime_column_is_supported():
    frame = pl.DataFrame({
        "ts": [datetime(2026, 1, 1, hour, tzinfo=timezone.utc) for hour in (2, 5, 8)],
        "value": [10.0, 20.0, 30.0],
    })
    train, evidence = split_training_source(
        frame.lazy(), timestamp_column="ts", train_end=temporal_config().train_end,
        calibration_end=temporal_config().calibration_end,
    )
    assert train.select("value").collect()["value"].to_list() == [10.0]
    assert evidence["test_rows"] == 1


def test_temporal_split_requires_three_nonempty_periods():
    with pytest.raises(ValueError, match="nonempty"):
        split_training_source(
            events().lazy(), timestamp_column="ts",
            train_end=datetime(2026, 1, 1, 4, tzinfo=timezone.utc),
            calibration_end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
