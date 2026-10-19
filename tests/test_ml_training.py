"""첫 ML 데이터 입력 계약과 학습 아티팩트 생성의 경계 검증."""

from contextlib import contextmanager

import joblib
import polars as pl
import pytest

from sos_platform.ml_training import MAX_ROWS, train_dataset, training_matrix


@contextmanager
def _scan(_store, dataset):
    yield dataset["frame"].lazy()


def test_training_matrix_keeps_feature_order_and_rejects_bad_inputs(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_training.pinned_scan_dataset", _scan)
    dataset = {"frame": pl.DataFrame({"a": [1.0, 3.0], "b": [2, 4]})}
    values = training_matrix(None, dataset, ["b", "a"])
    assert values.shape == (2, 2)
    assert values.tolist() == [[2.0, 1.0], [4.0, 3.0]]
    for names in ([], ["a", "a"], ["missing"]):
        with pytest.raises(ValueError):
            training_matrix(None, dataset, names)


def test_training_requires_numeric_and_bounded_rows(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_training.pinned_scan_dataset", _scan)
    with pytest.raises(ValueError, match="numeric"):
        training_matrix(None, {"frame": pl.DataFrame({"text": ["x", "y"]})}, ["text"])
    with pytest.raises(ValueError, match="50000"):
        training_matrix(None, {"frame": pl.DataFrame({"x": range(MAX_ROWS + 1)})}, ["x"])


def test_training_writes_model_and_reports_training_only_statistics(tmp_path, monkeypatch):
    monkeypatch.setattr("sos_platform.ml_training.pinned_scan_dataset", _scan)
    dataset = {
        "version": "source-1",
        "frame": pl.DataFrame({"x": [0.1, 0.2, 0.3, 0.4, 10.0]}),
    }
    params = {
        "n_estimators": 10,
        "max_samples": "auto",
        "max_features": 1.0,
        "bootstrap": False,
        "random_state": 42,
    }
    pending = tmp_path / "generated.joblib"
    result = train_dataset(
        None,
        dataset,
        {
            "algorithm": "isolation_forest",
            "dataset_version": "source-1",
            "features": ["x"],
            "params": params,
        },
        pending,
    )
    assert pending.exists()
    assert joblib.load(pending).n_features_in_ == 1
    assert result["training_score_summary"]["scope"] == "training_data_only"
    assert "precision" not in result["training_score_summary"]
    with pytest.raises(ValueError, match="version"):
        train_dataset(
            None,
            dataset,
            {
                "algorithm": "isolation_forest",
                "dataset_version": "old",
                "features": ["x"],
                "params": params,
            },
            tmp_path / "should-not-exist.joblib",
        )


def test_training_refuses_integer_precision_loss(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_training.pinned_scan_dataset", _scan)
    safe = 2**53
    assert training_matrix(
        None, {"frame": pl.DataFrame({"x": [safe - 1, safe]})}, ["x"]
    ).tolist() == [[float(safe - 1)], [float(safe)]]
    for frame in (
        pl.DataFrame({"x": [0, safe + 1]}),
        pl.DataFrame({"x": [0, -safe - 1]}),
        pl.DataFrame({"x": pl.Series([0, 2**64 - 1], dtype=pl.UInt64)}),
    ):
        with pytest.raises(ValueError, match="exact float64"):
            training_matrix(None, {"frame": frame}, ["x"])


def test_training_rejects_null_numeric_inputs(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_training.pinned_scan_dataset", _scan)
    for series in (
        pl.Series("x", [None, None], dtype=pl.Int64),
        pl.Series("x", [0, None], dtype=pl.Int64),
    ):
        with pytest.raises(ValueError, match="null"):
            training_matrix(None, {"frame": pl.DataFrame({"x": series})}, ["x"])
