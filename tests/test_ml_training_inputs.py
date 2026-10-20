"""Training 입력 정책·실행 수용 경계와 연구 근거를 확인한다."""

from contextlib import contextmanager

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform import ml_training
from sos_platform.errors import PlatformError
from sos_platform.schemas import MLTrainRequest, MLTrainingInputPolicy


def all_rows() -> MLTrainingInputPolicy:
    return MLTrainingInputPolicy(mode="all")


def sample(rows: int, seed: int = 42) -> MLTrainingInputPolicy:
    return MLTrainingInputPolicy(mode="sample", rows=rows, seed=seed)


def bind_frame(monkeypatch, frame: pl.DataFrame | pl.LazyFrame) -> None:
    @contextmanager
    def scan(_store, _dataset):
        yield frame.lazy() if isinstance(frame, pl.DataFrame) else frame

    monkeypatch.setattr(ml_training, "pinned_scan_dataset", scan)


def test_all_input_over_50k_rows_and_32_features(monkeypatch):
    count = 50_017
    names = [f"f_{i}" for i in range(40)]
    values = np.arange(count, dtype=np.float64)
    bind_frame(monkeypatch, pl.DataFrame({name: values + i for i, name in enumerate(names)}))
    matrix, evidence = ml_training.training_matrix(
        None, {}, names, input_policy=all_rows(), worker_memory_mib=256
    )
    assert matrix.shape == (count, 40)
    assert evidence["source_rows"] == count
    assert evidence["method"] == "all_rows"
    assert evidence["requested_rows"] is None
    assert evidence["seed"] is None


def test_sample_is_independent_of_worker_memory_budget(monkeypatch):
    count = 50_017
    names = [f"f_{i}" for i in range(40)]
    values = np.arange(count, dtype=np.float64)
    bind_frame(monkeypatch, pl.DataFrame({name: values + i for i, name in enumerate(names)}))
    first, earlier = ml_training.training_matrix(
        None, {}, names, input_policy=sample(900, 17), worker_memory_mib=4
    )
    second, later = ml_training.training_matrix(
        None, {}, names, input_policy=sample(900, 17), worker_memory_mib=256
    )
    np.testing.assert_array_equal(first, second)
    assert earlier == later
    assert earlier["source_rows"] == count
    assert earlier["selected_rows"] == earlier["requested_rows"] == 900
    assert earlier["method"] == "row_priority_bottom_k_v1"


def test_full_input_exceeding_worker_budget_is_not_truncated(monkeypatch):
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(20_000, dtype=float)}))
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=all_rows(), worker_memory_mib=1
        )
    assert exc.value.code == "SOS-ML-TRAIN-CAPACITY"


def test_requested_sample_exceeding_budget_is_not_shrunk(monkeypatch):
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(20_000, dtype=float)}))
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(5000), worker_memory_mib=1
        )
    assert exc.value.code == "SOS-ML-TRAIN-CAPACITY"


def test_sample_larger_than_dataset_fails(monkeypatch):
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(50, dtype=float)}))
    with pytest.raises(ValueError, match="Sample rows exceed Dataset"):
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(100), worker_memory_mib=4
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "sample"},
        {"mode": "sample", "rows": 100},
        {"mode": "sample", "seed": 2},
        {"mode": "all", "rows": 100},
        {"mode": "all", "seed": 2},
        {"mode": "sample", "rows": True, "seed": 2},
        {"mode": "sample", "rows": 1, "seed": 2},
        {"mode": "sample", "rows": 50, "seed": -1},
    ],
)
def test_invalid_input_selection_rejected_by_api_contract(payload):
    with pytest.raises(ValidationError):
        MLTrainingInputPolicy.model_validate(payload)


@pytest.mark.parametrize("invalid", ["null", "nan", "infinity", "large_integer"])
def test_sample_checks_invalid_values_outside_selection(monkeypatch, invalid):
    count = 20_000
    values = np.arange(count, dtype=np.float64)
    data = {"x": values.copy(), "y": values * 2}
    if invalid == "large_integer":
        data["x"] = np.arange(count, dtype=np.int64)
        data["x"][-1] = 2**53 + 2
        expected = "exact float64"
    elif invalid == "null":
        frame = pl.DataFrame(data).with_columns(
            pl.when(pl.int_range(pl.len()) == count - 1)
            .then(None)
            .otherwise(pl.col("x"))
            .alias("x")
        )
        expected = "null values"
    else:
        data["x"][-1] = np.nan if invalid == "nan" else np.inf
        expected = "finite numeric"
    if invalid != "null":
        frame = pl.DataFrame(data)
    bind_frame(monkeypatch, frame)
    with pytest.raises(ValueError, match=expected):
        ml_training.training_matrix(
            None, {}, ["x", "y"], input_policy=sample(1000), worker_memory_mib=4
        )


def test_duplicate_feature_values_preserve_frequency(monkeypatch):
    signal = np.concatenate((np.zeros(9000), np.ones(1000)))
    bind_frame(monkeypatch, pl.DataFrame({"signal": signal}))
    matrix, evidence = ml_training.training_matrix(
        None, {}, ["signal"], input_policy=sample(1800, 29), worker_memory_mib=4
    )
    assert evidence["selected_rows"] == 1800
    assert 0.84 < np.mean(matrix[:, 0] == 0.0) < 0.96


def test_sample_input_fingerprint_reflects_scan_order(monkeypatch):
    data = pl.DataFrame({"x": np.arange(20_000, dtype=float)})
    bind_frame(monkeypatch, data)
    _, first = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(1000), worker_memory_mib=4
    )
    bind_frame(monkeypatch, data.reverse())
    _, second = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(1000), worker_memory_mib=4
    )
    assert first["selection_sha256"] != second["selection_sha256"]


def test_sample_matches_global_row_priority_reference(monkeypatch):
    count, requested, seed = 5000, 1000, 23
    values = np.arange(count, dtype=np.float64)
    bind_frame(monkeypatch, pl.DataFrame({"x": values}))
    matrix, evidence = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(requested, seed), worker_memory_mib=4
    )
    priorities = np.random.default_rng(seed).integers(
        0, np.iinfo(np.uint64).max, size=count, dtype=np.uint64
    )
    chosen = np.argpartition(priorities, requested - 1)[:requested]
    np.testing.assert_array_equal(matrix[:, 0], np.sort(values[chosen]))
    assert evidence["source_rows"] == count


def test_full_dataset_keeps_original_feature_sort(monkeypatch):
    frame = pl.DataFrame({"x": [3., 1., 2., 1.], "y": [5., 2., 4., 1.]})
    bind_frame(monkeypatch, frame)
    first, a = ml_training.training_matrix(
        None, {}, ["x", "y"], input_policy=all_rows(), worker_memory_mib=4
    )
    bind_frame(monkeypatch, frame.reverse())
    second, b = ml_training.training_matrix(
        None, {}, ["x", "y"], input_policy=all_rows(), worker_memory_mib=4
    )
    np.testing.assert_array_equal(first, frame.sort(["x", "y"]).to_numpy())
    np.testing.assert_array_equal(first, second)
    assert a["selection_sha256"] == b["selection_sha256"]


def test_model_random_state_does_not_change_input_selection(monkeypatch, tmp_path):
    values = np.arange(4000, dtype=np.float64)
    bind_frame(monkeypatch, pl.DataFrame({"x": values}))
    def run(random_state: int):
        return ml_training.train_dataset(
            None, {"version": "v1"},
            {"algorithm": "isolation_forest", "dataset_version": "v1",
             "features": ["x"], "params": {"n_estimators": 8, "random_state": random_state},
             "training_input": sample(400, 9).model_dump()},
            tmp_path / f"model-{random_state}.joblib",
        )
    first = run(3)
    second = run(17)
    assert first["training_selection"] == second["training_selection"]
    assert first["params"]["random_state"] != second["params"]["random_state"]
    assert first["training_score_summary"]["scope"] == "training_data_only"
    assert first["runtime"]["training_selection"] == first["training_selection"]


def test_legacy_job_without_policy_uses_all_input(monkeypatch, tmp_path):
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(120, dtype=float)}))
    result = ml_training.train_dataset(
        None, {"version": "v1"},
        {"algorithm": "isolation_forest", "dataset_version": "v1",
         "features": ["x"], "params": {"n_estimators": 8, "random_state": 42}},
        tmp_path / "model.joblib",
    )
    assert result["source_rows"] == result["training_rows"] == 120
    assert result["training_selection"]["method"] == "all_rows"


def test_lazy_parquet_input_selection(monkeypatch, tmp_path):
    count = 100_029
    path = tmp_path / "large.parquet"
    pl.DataFrame({
        "x": np.arange(count, dtype=np.float64),
        "y": np.arange(count, dtype=np.float64) * 3,
    }).write_parquet(path)
    bind_frame(monkeypatch, pl.scan_parquet(path))
    matrix, evidence = ml_training.training_matrix(
        None, {}, ["x", "y"], input_policy=sample(1000, 7), worker_memory_mib=4
    )
    assert matrix.shape == (1000, 2)
    assert evidence["source_rows"] == count


def test_api_defaults_and_scoring_contract():
    from sos_platform.ml_scoring import MAX_SCORING_ROWS

    default = MLTrainRequest(dataset_id="d1", features=[f"f_{i}" for i in range(40)])
    assert default.training_input == all_rows()
    assert MLTrainRequest(
        dataset_id="d1", features=["x"], training_input={"mode": "sample", "rows": 100, "seed": 7}
    ).training_input == sample(100, 7)
    assert MAX_SCORING_ROWS == 50_000
