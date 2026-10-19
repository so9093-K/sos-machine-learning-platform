"""신뢰된 모델 기반 Dataset Scoring 계약."""

from contextlib import contextmanager

import polars as pl
import pytest

from sos_platform.ml_isolation_forest import train_isolation_forest
from sos_platform.ml_scoring import run_dataset_scoring


@contextmanager
def _scan(_store, dataset):
    yield dataset["frame"].lazy()


class StubStore:
    home = None

    def __init__(self, revision):
        from pathlib import Path

        self.home = Path("/tmp")
        self.revision = revision

    def get_model_revision(self, identifier):
        if identifier == self.revision["id"]:
            return self.revision
        return None


def _revision():
    return {
        "id": "model1",
        "features": ["temperature"],
        "runtime": {"feature_dtypes": ["Float64"]},
        "owner_actor": "alice",
    }


def _spec(dataset_version, label=None):
    return {
        "model_revision_id": "model1",
        "model_owner_actor": "alice",
        "dataset_version": dataset_version,
        "label_column": label,
    }


def test_scoring_creates_rowwise_immutable_artifact_and_rank_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr("sos_platform.ml_scoring.pinned_scan_dataset", _scan)
    trained = train_isolation_forest([[1.0], [2.0], [3.0], [4.0]])
    monkeypatch.setattr(
        "sos_platform.ml_scoring.load_trusted_isolation_forest",
        lambda revision, scratch: trained,
    )
    frame = pl.DataFrame({
        "temperature": [1.0, 2.0, 100.0, 3.0],
        "flag": [0, 0, 1, 0],
    })
    destination = tmp_path / "scores.parquet"
    metadata = run_dataset_scoring(
        StubStore(_revision()),
        {"version": "v1", "frame": frame},
        _spec("v1", "flag"),
        destination,
    )
    result = pl.read_parquet(destination)
    assert result["row_index"].to_list() == [0, 1, 2, 3]
    assert result["features"].struct.field("temperature").to_list() == frame["temperature"].to_list()
    assert result["label"].to_list() == [0, 0, 1, 0]
    assert result["anomaly_score"].to_list() == list(
        trained.score_anomalies(frame.select("temperature").to_numpy())
    )
    assert metadata["score_summary"]["evaluation_kind"] == "unlabeled"
    assert metadata["ranking_metrics"]["positive_count"] == 1
    assert metadata["ranking_metrics"]["average_precision"] is not None


def test_scoring_rejects_wrong_schema_and_incomplete_labels(tmp_path, monkeypatch):
    monkeypatch.setattr("sos_platform.ml_scoring.pinned_scan_dataset", _scan)
    monkeypatch.setattr(
        "sos_platform.ml_scoring.load_trusted_isolation_forest",
        lambda revision, scratch: train_isolation_forest([[1.0], [2.0]]),
    )
    rows = [
        (pl.DataFrame({"temperature": [1, 2]}), _spec("v1"), "dtypes"),
        (pl.DataFrame({"temperature": [1.0, None]}), _spec("v1"), "null"),
        (pl.DataFrame({"temperature": [1.0, 2.0], "flag": [0, None]}),
         _spec("v1", "flag"), "labels"),
        (pl.DataFrame({"temperature": [1.0, 2.0], "flag": [0, 3]}),
         _spec("v1", "flag"), "binary"),
    ]
    for frame, spec, reason in rows:
        with pytest.raises(ValueError, match=reason):
            run_dataset_scoring(
                StubStore(_revision()), {"version": "v1", "frame": frame}, spec,
                tmp_path / "invalid.parquet",
            )
    with pytest.raises(ValueError, match="revision"):
        run_dataset_scoring(
            StubStore(_revision()),
            {"version": "v2", "frame": pl.DataFrame({"temperature": [1.0]})},
            _spec("v1"),
            tmp_path / "invalid.parquet",
        )
