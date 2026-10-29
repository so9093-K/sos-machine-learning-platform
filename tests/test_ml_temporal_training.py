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


def test_time_split_applies_before_sampling_even_when_later_rows_are_extreme(monkeypatch):
    from sos_platform.ml_input import TRAINING_SELECTION_LEGACY

    bind_frame(monkeypatch, events())
    selected, evidence = ml_training.training_matrix(
        None, {"version": "d1"}, ["value"],
        input_policy=MLTrainingInputPolicy(mode="sample", rows=2, seed=11),
        selection_algorithm_version=TRAINING_SELECTION_LEGACY,
        temporal_split=temporal_config(), missing_policy="train_median",
        worker_memory_mib=64,
    )
    assert selected.shape == (2, 1)
    assert np.nanmax(selected) <= 9.0
    assert evidence["source_rows"] == 4
    assert evidence["selected_rows"] == 2


def test_http_submit_and_fork_preserve_temporal_boundaries_and_missing_policy():
    from unittest.mock import Mock

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from sos_platform.api_ml_training import TRAIN_MODEL_DEP, training_router
    from sos_platform.security import Actor

    store = Mock()
    actor = Actor("alice", "human", frozenset({"model_builder"}))
    spec = {
        "algorithm": "isolation_forest",
        "dataset_version": "d1",
        "features": ["value"],
        "params": {"n_estimators": 8, "random_state": 42},
        "training_input": {"mode": "all"},
        "missing_policy": "train_median",
        "temporal_split": temporal_config().model_dump(mode="json"),
    }
    source_job = {
        "id": "existing", "kind": "ml_train", "status": "Succeeded",
        "dataset_id": "d1", "spec": spec,
    }

    def created(**kwargs):
        return {
            "id": "created", "kind": kwargs["kind"], "dataset_id": kwargs["dataset_id"],
            "spec": kwargs["spec"], "status": "Queued", "cancel_requested": False,
            "result": None, "error": None, "attempts": [],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }

    store.create_job.side_effect = created
    app = FastAPI()
    app.include_router(training_router(
        store,
        existing_dataset=lambda dataset_id, _actor: {"id": dataset_id, "version": "d1"},
        existing_job=lambda _job_id, _actor: source_job,
        require_submission_key=lambda _: None,
    ))
    app.dependency_overrides[TRAIN_MODEL_DEP.dependency] = lambda: actor

    @app.middleware("http")
    async def set_request_id(request: Request, call_next):
        request.state.request_id = "temporal-test"
        return await call_next(request)

    with TestClient(app) as client:
        result = client.post("/api/ml/train/jobs", json={
            "dataset_id": "d1", "features": ["value"],
            "missing_policy": "train_median", "temporal_split": spec["temporal_split"],
        })
        assert result.status_code == 202, result.text
        received = store.create_job.call_args.kwargs["spec"]
        assert received["missing_policy"] == "train_median"
        assert received["temporal_split"] == spec["temporal_split"]

        fork = client.post("/api/ml/train/jobs/existing/fork", json={})
        assert fork.status_code == 202, fork.text
        restored = store.create_job.call_args.kwargs["spec"]
        assert restored["missing_policy"] == "train_median"
        assert restored["temporal_split"] == spec["temporal_split"]

        invalid = client.post("/api/ml/train/jobs", json={
            "dataset_id": "d1", "features": ["value"],
            "temporal_split": {
                "timestamp_column": "ts",
                "train_end": "2026-01-01T09:00:00",
                "calibration_end": "2026-01-01T10:00:00Z",
            },
        })
        assert invalid.status_code == 422


@pytest.mark.parametrize("utc_text", [
    "2026-01-01 02:00:00+00",
    "2026-01-01 02:00:00+0000",
    "2026-01-01 02:00:00+00:00",
    "2026-01-01T02:00:00Z",
    "2026-01-01 11:00:00+09",
    "2026-01-01 11:00:00+0900",
    "2026-01-01 11:00:00+09:00",
])
def test_explicit_offset_formats_from_real_parquet_normalize_to_utc(utc_text):
    # 실파일 ts의 예: "2025-11-13 21:21:00+00" (오프셋은 명시됨).
    frame = pl.DataFrame({
        "ts": [utc_text, "2026-01-01 05:00:00+00", "2026-01-01 08:00:00+00"],
        "value": [10.0, 20.0, 30.0],
    })
    train, evidence = split_training_source(
        frame.lazy(), timestamp_column="ts",
        train_end=temporal_config().train_end,
        calibration_end=temporal_config().calibration_end,
    )
    assert train.select("value").collect()["value"].to_list() == [10.0]
    assert (evidence["train_rows"], evidence["calibration_rows"], evidence["test_rows"]) == (
        1, 1, 1
    )


def test_nullable_integer_feature_with_all_null_first_batch_is_not_typeerror(monkeypatch):
    # 8192행 첫 Batch는 모두 null, 그 다음 Batch에 유효값이 있다.
    # Train 전체 중앙값을 Fit할 수 있어야 한다.
    from sos_platform.ml_if_preprocessing import fit_if_median_imputer

    values = pl.Series("count", [None] * 8192 + [3, 7], dtype=pl.Int64)
    bind_frame(monkeypatch, pl.DataFrame({"count": values}))
    matrix, evidence = ml_training.training_matrix(
        None, {"version": "d1"}, ["count"],
        input_policy=MLTrainingInputPolicy(mode="all"),
        missing_policy="train_median",
        worker_memory_mib=256,
    )
    fitted = fit_if_median_imputer(matrix, feature_names=["count"])
    assert fitted.medians == (5.0,)
    assert evidence["source_rows"] == 8194


def test_completely_null_integer_training_feature_reports_model_input_error(monkeypatch):
    from sos_platform.ml_if_preprocessing import fit_if_median_imputer

    bind_frame(monkeypatch, pl.DataFrame({
        "count": pl.Series([None, None, None], dtype=pl.Int64),
    }))
    matrix, _ = ml_training.training_matrix(
        None, {"version": "d1"}, ["count"],
        input_policy=MLTrainingInputPolicy(mode="all"),
        missing_policy="train_median", worker_memory_mib=256,
    )
    with pytest.raises(ValueError, match="Entirely missing"):
        fit_if_median_imputer(matrix, feature_names=["count"])
