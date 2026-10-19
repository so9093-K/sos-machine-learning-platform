"""신뢰된 Isolation Forest 모델 복원·호환성 검사."""

import hashlib
from pathlib import Path

import joblib
import numpy as np
import pytest

from sos_platform.ml_isolation_forest import IsolationForestConfig, train_isolation_forest
from sos_platform.ml_runtime import assert_model_runtime_compatible, load_trusted_isolation_forest
from sos_platform.object_store import ObjectStore


def _revision() -> dict:
    import platform

    import sklearn

    config = IsolationForestConfig(n_estimators=10, random_state=5)
    return {
        "id": "r1",
        "algorithm": "isolation_forest",
        "job_id": "job1",
        "attempt_id": "attempt1",
        "location": "s3://test/results/job1/attempt1.model.joblib",
        "sha256": "a" * 64,
        "features": ["x", "y"],
        "params": config.__dict__,
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
            "model_format": "joblib",
            "loading": "trusted_worker_only",
            "feature_dtypes": ["Float64", "Float64"],
        },
    }


def test_runtime_rejects_incompatible_and_missing_signatures() -> None:
    revision = _revision()
    assert_model_runtime_compatible(revision)
    for field, value in (("python", "0.0"), ("scikit_learn", "0.0"), ("numpy", "0.0")):
        bad = {**revision, "runtime": {**revision["runtime"], field: value}}
        with pytest.raises(ValueError, match="incompatible"):
            assert_model_runtime_compatible(bad)
    without_schema = {**revision, "runtime": {**revision["runtime"], "feature_dtypes": None}}
    with pytest.raises(ValueError, match="signature"):
        assert_model_runtime_compatible(without_schema)


def test_verified_model_roundtrip_and_rejects_modified_bytes(tmp_path, monkeypatch) -> None:
    data = np.array([[1.0, 2.0], [2.0, 4.0], [5.0, 6.0], [30.0, 40.0]])
    model = train_isolation_forest(data, IsolationForestConfig(n_estimators=10, random_state=5))
    saved = tmp_path / "source.joblib"
    joblib.dump(model.estimator, saved)
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    revision = {**_revision(), "sha256": digest}

    class Storage:
        def location(self, key: str) -> str:
            return "s3://test/" + key

        def get(self, key: str, destination: Path, *, expected_sha256: str):
            data = saved.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_sha256:
                raise ValueError("Artifact digest mismatch")
            destination.write_bytes(data)

    monkeypatch.setattr("sos_platform.ml_runtime.ObjectStore", Storage)
    restored = load_trusted_isolation_forest(revision, tmp_path)
    assert restored.score_anomalies(data) == model.score_anomalies(data)
    saved.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="digest"):
        load_trusted_isolation_forest(revision, tmp_path)


def test_model_rejects_location_and_type_before_deserialization(tmp_path, monkeypatch) -> None:
    class UnexpectedStorage:
        def location(self, key: str) -> str:
            return "s3://test/" + key

        def get(self, key: str, destination: Path, *, expected_sha256: str):
            raise AssertionError("Deserialization should not be reached")

    monkeypatch.setattr("sos_platform.ml_runtime.ObjectStore", UnexpectedStorage)
    revision = {**_revision(), "location": "s3://test/datasets/external.joblib"}
    with pytest.raises(ValueError, match="Attempt location"):
        load_trusted_isolation_forest(revision, tmp_path)
