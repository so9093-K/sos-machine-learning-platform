"""신뢰된 Model Revision 번들의 학습·복원·Scoring·설명 연결을 검증한다."""

import hashlib
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import polars as pl
import pytest

from sos_platform import ml_decision, ml_runtime, ml_scoring, ml_training
from sos_platform.ml_if_preprocessing import FittedIFMedianImputer


class LocalObjects:
    """Job/Attempt 경로와 SHA-256을 확인하는 테스트용 객체 저장소."""

    def __init__(self, files: dict[str, Path]):
        self.files = files

    def location(self, key: str) -> str:
        return f"mock://{key}"

    def get(self, key: str, destination: Path, *, expected_sha256: str) -> None:
        file = self.files[key]
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise ValueError("Artifact integrity mismatch")
        shutil.copyfile(file, destination)


@pytest.fixture
def trained_bundle(monkeypatch, tmp_path):
    """완전 입력으로 새 모델을 학습하고 객체 게시된 모델 판본을 구성한다."""
    (tmp_path / "cache").mkdir()
    training = pl.DataFrame({
        "volume": [1.0, 2.0, 3.0, 4.0, 5.0, 9.0],
        "count": [10.0, 12.0, 11.0, 15.0, 20.0, 30.0],
    })

    @contextmanager
    def scan_training(_store, _dataset):
        yield training.lazy()

    monkeypatch.setattr(ml_training, "pinned_scan_dataset", scan_training)
    model_file = tmp_path / "saved-model.joblib"
    result = ml_training.train_dataset(
        None,
        {"id": "data-a", "version": "train-v1"},
        {
            "algorithm": "isolation_forest",
            "dataset_version": "train-v1",
            "features": ["volume", "count"],
            "params": {"n_estimators": 8, "random_state": 4},
            "training_input": {"mode": "all"},
        },
        model_file,
    )
    key = "results/job-1/attempt-1.model.joblib"
    files = {key: model_file}
    storage = LocalObjects(files)
    monkeypatch.setattr(ml_runtime, "ObjectStore", lambda: storage)
    monkeypatch.setattr(ml_decision, "ObjectStore", lambda: storage)
    revision = {
        "id": "model-1",
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "dataset_id": "data-a",
        "dataset_version": "train-v1",
        "algorithm": "isolation_forest",
        "location": storage.location(key),
        "sha256": hashlib.sha256(model_file.read_bytes()).hexdigest(),
        "runtime": result["runtime"],
        "features": result["features"],
        "params": result["params"],
        "owner_actor": "owner-1",
    }
    store = SimpleNamespace(
        home=tmp_path,
        get_model_revision=lambda name: revision if name == "model-1" else None,
    )
    return store, revision, storage, files, model_file


def test_train_artifact_keeps_fitted_preprocessing_and_revision_evidence(trained_bundle):
    store, revision, _, _, path = trained_bundle
    bundle = joblib.load(path)
    assert bundle["schema"] == "if_model_with_preprocessing_v1"
    assert type(bundle["preprocessing"]) is FittedIFMedianImputer
    assert bundle["preprocessing"].feature_names == ("volume", "count")
    assert list(bundle["preprocessing"].medians) == revision["runtime"]["feature_reference_medians"]
    assert revision["runtime"]["preprocessing_mode"] == "train_median_v1"
    restored = ml_runtime.load_trusted_isolation_forest(revision, store.home)
    assert restored.preprocessing == bundle["preprocessing"]


def test_restored_bundle_applies_same_training_medians_to_unseen_nulls(trained_bundle):
    store, revision, _, _, _ = trained_bundle
    restored = ml_runtime.load_trusted_isolation_forest(revision, store.home)
    median = revision["runtime"]["feature_reference_medians"][0]
    with_null = restored.score_anomalies([[np.nan, 17.0]])
    with_median = restored.score_anomalies([[median, 17.0]])
    assert with_null == with_median


def test_scoring_and_explanation_use_same_bundle_without_refitting(
    monkeypatch, trained_bundle, tmp_path
):
    store, revision, storage, files, _ = trained_bundle
    evaluated = pl.DataFrame({
        "volume": [None, 3.0, 50.0],
        "count": [16.0, 20.0, 1.0],
    })

    @contextmanager
    def scan_evaluation(_store, _dataset):
        yield evaluated.lazy()

    monkeypatch.setattr(ml_scoring, "pinned_scan_dataset", scan_evaluation)
    pending = tmp_path / "saved-scores.parquet"
    spec = {
        "model_revision_id": revision["id"],
        "model_owner_actor": revision["owner_actor"],
        "dataset_version": "test-v1",
    }
    summary = ml_scoring.run_dataset_scoring(
        store, {"id": "data-b", "version": "test-v1"}, spec, pending
    )
    assert summary["row_count"] == 3
    scores = pl.read_parquet(pending)
    restored = ml_runtime.load_trusted_isolation_forest(revision, store.home)
    median = revision["runtime"]["feature_reference_medians"][0]
    expected = restored.score_anomalies([[median, 16.0], [3.0, 20.0], [50.0, 1.0]])
    np.testing.assert_allclose(scores["anomaly_score"].to_numpy(), expected)

    scoring_key = "results/score-1/attempt-2.scores.parquet"
    files[scoring_key] = pending
    scoring = {
        "id": "scoring-1",
        "job_id": "score-1",
        "attempt_id": "attempt-2",
        "model_revision_id": revision["id"],
        "owner_actor": revision["owner_actor"],
        "row_count": 3,
        "location": storage.location(scoring_key),
        "sha256": hashlib.sha256(pending.read_bytes()).hexdigest(),
    }
    store.get_scoring_result = lambda name: scoring if name == "scoring-1" else None
    from sos_platform.ml_explanation import explain_scored_row

    explanation = explain_scored_row(
        store, {"scoring_result_id": scoring["id"], "source_owner_actor": "owner-1",
                "row_index": 0}
    )
    assert explanation["score"] == pytest.approx(expected[0])
    first = explanation["feature_sensitivities"][0]
    assert first["feature"] == "volume"
    assert first["missing_imputed"] is True
    assert first["actual_value"] is None
    assert first["anomaly_score_delta"] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("field, changed", [
    ("features", ["count", "volume"]),
    ("reference", [123.0, 456.0]),
    ("schema", "unknown_bundle"),
    ("mode", "unknown_mode"),
])
def test_corrupted_revision_contract_rejected(trained_bundle, field, changed):
    store, revision, _, _, _ = trained_bundle
    tampered = {**revision, "runtime": {**revision["runtime"]}}
    if field == "features":
        tampered["features"] = changed
    elif field == "reference":
        tampered["runtime"]["feature_reference_medians"] = changed
    elif field == "schema":
        tampered["runtime"]["artifact_schema"] = changed
    else:
        tampered["runtime"]["preprocessing_mode"] = changed
    with pytest.raises(ValueError):
        ml_runtime.load_trusted_isolation_forest(tampered, store.home)


def test_legacy_estimator_only_revision_can_still_be_loaded(trained_bundle, tmp_path):
    store, revision, storage, files, path = trained_bundle
    earlier = tmp_path / "legacy.joblib"
    joblib.dump(joblib.load(path)["estimator"], earlier)
    key = "results/old-job/old-attempt.model.joblib"
    files[key] = earlier
    legacy = {**revision, "job_id": "old-job", "attempt_id": "old-attempt",
              "location": storage.location(key),
              "sha256": hashlib.sha256(earlier.read_bytes()).hexdigest(),
              "runtime": {**revision["runtime"]}}
    legacy["runtime"].pop("artifact_schema")
    legacy["runtime"].pop("preprocessing_mode")
    restored = ml_runtime.load_trusted_isolation_forest(legacy, store.home)
    assert restored.preprocessing is None
    assert len(restored.score_anomalies([[1.0, 10.0]])) == 1
    with pytest.raises(ValueError, match="finite"):
        restored.score_anomalies([[np.nan, 10.0]])
