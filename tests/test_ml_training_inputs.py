"""ML Training은 고정 행/Feature 수가 아닌 Worker 예산과 입력 계약으로 판단한다."""

from contextlib import contextmanager

import numpy as np
import polars as pl
import pytest

from sos_platform import ml_training


def bind_frame(monkeypatch, frame: pl.DataFrame | pl.LazyFrame) -> None:
    @contextmanager
    def scan(_store, _dataset):
        yield frame.lazy() if isinstance(frame, pl.DataFrame) else frame

    monkeypatch.setattr(ml_training, "pinned_scan_dataset", scan)


def test_large_wide_training_input_can_use_full_dataset(monkeypatch):
    count = 50_017
    names = [f"f_{i}" for i in range(40)]
    numbers = np.arange(count, dtype=np.float64)
    bind_frame(monkeypatch, pl.DataFrame({name: numbers + i for i, name in enumerate(names)}))
    matrix, selection = ml_training.training_matrix(
        None, {}, names, seed=42, worker_memory_mib=256
    )
    assert matrix.shape == (count, 40)
    assert selection["source_rows"] == count
    assert selection["method"] == "all_rows"
    assert selection["selection_sha256"]


def test_budgeted_selection_covers_all_rows_and_is_scan_order_independent(monkeypatch):
    count = 50_017
    names = [f"f_{i}" for i in range(40)]
    numbers = np.arange(count, dtype=np.float64)
    data = pl.DataFrame({name: numbers + i for i, name in enumerate(names)})
    bind_frame(monkeypatch, data)
    first, original = ml_training.training_matrix(
        None, {}, names, seed=42, worker_memory_mib=4
    )
    assert 2 < first.shape[0] < count
    assert original["method"] == "feature_hash_bottom_k_v1"
    assert original["source_rows"] == count

    bind_frame(monkeypatch, data.reverse())
    second, reversed_input = ml_training.training_matrix(
        None, {}, names, seed=42, worker_memory_mib=4
    )
    np.testing.assert_array_equal(first, second)
    assert original == reversed_input


@pytest.mark.parametrize("invalid", ["null", "nan", "infinity", "large_integer"])
def test_budgeted_training_checks_invalid_values_outside_selected_rows(monkeypatch, invalid):
    count = 20_000
    values = np.arange(count, dtype=np.float64)
    data = {"x": values, "y": values * 2}
    if invalid == "large_integer":
        data["x"] = np.arange(count, dtype=np.int64)
        data["x"][-1] = 2**53 + 2
        expected = "exact float64"
    else:
        data["x"] = values.copy()
        if invalid == "null":
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
            None, {}, ["x", "y"], seed=42, worker_memory_mib=1
        )


def test_training_job_retains_selection_evidence_with_model(monkeypatch, tmp_path):
    values = np.linspace(0, 10, 120)
    bind_frame(monkeypatch, pl.DataFrame({"x": values, "y": values**2}))
    output = tmp_path / "model.joblib"
    result = ml_training.train_dataset(
        None,
        {"version": "v1"},
        {
            "algorithm": "isolation_forest",
            "dataset_version": "v1",
            "features": ["x", "y"],
            "params": {"n_estimators": 8, "random_state": 42},
        },
        output,
    )
    assert output.is_file()
    assert result["source_rows"] == result["training_rows"] == 120
    assert result["runtime"]["training_selection"] == result["training_selection"]
    assert result["training_score_summary"]["scope"] == "training_data_only"

def test_training_request_accepts_more_than_32_features():
    from sos_platform.schemas import MLTrainRequest

    request = MLTrainRequest(dataset_id="d1", features=[f"f_{n}" for n in range(40)])
    assert len(request.features) == 40


def test_scoring_limit_is_not_inherited_from_training():
    from sos_platform.ml_scoring import MAX_SCORING_ROWS

    assert MAX_SCORING_ROWS == 50_000


def test_training_scans_actual_parquet_lazily(monkeypatch, tmp_path):
    count = 100_029
    source = tmp_path / "large.parquet"
    pl.DataFrame({
        "signal_a": np.arange(count, dtype=np.float64),
        "signal_b": np.arange(count, dtype=np.float64) * 3,
    }).write_parquet(source)
    bind_frame(monkeypatch, pl.scan_parquet(source))
    matrix, selection = ml_training.training_matrix(
        None, {}, ["signal_a", "signal_b"], seed=7, worker_memory_mib=4
    )
    assert 2 < matrix.shape[0] < count
    assert selection["source_rows"] == count
    assert selection["method"] == "feature_hash_bottom_k_v1"
