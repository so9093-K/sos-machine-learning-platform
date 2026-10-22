"""IF Reference Journey의 실제 계산 순서를 독립 판본들로 통합 검증한다.

테스트는 합성 Dataset과 로컬 아티팩트만 사용한다. 실제 DB/S3 배포 검증은 아니다.
"""

import hashlib
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from sos_platform import ml_decision, ml_runtime, ml_scoring, ml_training
from sos_platform.ml_explanation import explain_scored_row
from sos_platform.observation_preparation import materialize_observation_ids


class LocalAttemptObjects:
    """Attempt별 객체 경로와 체크섬을 확인하는 테스트용 객체 저장소."""

    def __init__(self) -> None:
        self.files: dict[str, Path] = {}

    def location(self, key: str) -> str:
        return f"mock://{key}"

    def add(self, key: str, path: Path) -> dict:
        self.files[key] = path
        return {
            "location": self.location(key),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def get(self, key: str, destination: Path, *, expected_sha256: str) -> None:
        src = self.files[key]
        if hashlib.sha256(src.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError("Attempt artifact checksum mismatch")
        shutil.copyfile(src, destination)


def test_prepare_train_calibrate_test_evaluate_explain_journey(monkeypatch, tmp_path):
    (tmp_path / "cache").mkdir()
    raw = pl.DataFrame({
        # 실제 ml_features.parquet에서 확인한 명시적 UTC +00 표기를 사용한다.
        "ts": [f"2026-01-01 {hour:02d}:00:00+00" for hour in range(9)],
        "count": pl.Series(
            "count", [1.0, 2.0, None, 4.0, 9.0, 12.0, 14.0, 20.0, 50.0],
            dtype=pl.Float64,
        ),
    })
    prepared = tmp_path / "published-dataset.parquet"
    preparation = materialize_observation_ids(
        raw.lazy(), prepared, column="observation_uuid"
    )
    assert preparation["output_rows"] == 9
    dataset = {
        "id": "prepared-dataset",
        "version": hashlib.sha256(prepared.read_bytes()).hexdigest(),
    }

    @contextmanager
    def pinned_scan(_store, selected):
        assert selected["id"] == dataset["id"]
        assert selected["version"] == dataset["version"]
        yield pl.scan_parquet(prepared)

    monkeypatch.setattr(ml_training, "pinned_scan_dataset", pinned_scan)
    monkeypatch.setattr(ml_scoring, "pinned_scan_dataset", pinned_scan)

    objects = LocalAttemptObjects()
    monkeypatch.setattr(ml_runtime, "ObjectStore", lambda: objects)
    monkeypatch.setattr(ml_decision, "ObjectStore", lambda: objects)

    store = SimpleNamespace(home=tmp_path)
    scope = {
        "timestamp_column": "ts",
        "train_end": "2026-01-01T04:00:00Z",
        "calibration_end": "2026-01-01T07:00:00Z",
    }
    train_file = tmp_path / "trained.joblib"
    trained = ml_training.train_dataset(
        store,
        dataset,
        {
            "algorithm": "isolation_forest",
            "dataset_version": dataset["version"],
            "features": ["count"],
            "params": {"n_estimators": 8, "random_state": 42},
            "training_input": {"mode": "all"},
            "missing_policy": "train_median",
            "temporal_split": scope,
        },
        train_file,
    )
    assert trained["training_rows"] == 4
    assert trained["runtime"]["feature_reference_medians"] == [2.0]
    assert trained["training_selection"]["temporal_split"]["calibration_rows"] == 3
    assert trained["training_selection"]["temporal_split"]["test_rows"] == 2
    model = {
        "id": "model-v1",
        "job_id": "training-job",
        "attempt_id": "training-attempt",
        "owner_actor": "analyst",
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "algorithm": "isolation_forest",
        "features": trained["features"],
        "params": trained["params"],
        "runtime": trained["runtime"],
        **objects.add(
            "results/training-job/training-attempt.model.joblib", train_file
        ),
    }
    store.get_model_revision = lambda mid: model if mid == model["id"] else None

    scoring_revisions = {}
    original_ids = pl.read_parquet(prepared)["observation_uuid"].to_list()
    for partition, indices in (("calibration", slice(4, 7)), ("test", slice(7, 9))):
        out = tmp_path / f"{partition}.scores.parquet"
        result = ml_scoring.run_dataset_scoring(
            store,
            dataset,
            {
                "model_revision_id": model["id"],
                "model_owner_actor": "analyst",
                "dataset_version": dataset["version"],
                "observation_id_column": "observation_uuid",
                "temporal_scope": {**scope, "partition": partition},
            },
            out,
        )
        scored = pl.read_parquet(out)
        assert scored["observation_id"].to_list() == original_ids[indices]
        key = f"results/{partition}-job/{partition}-attempt.scores.parquet"
        scoring_revisions[partition] = {
            "id": partition,
            "job_id": f"{partition}-job",
            "attempt_id": f"{partition}-attempt",
            "owner_actor": "analyst",
            "dataset_id": dataset["id"],
            "dataset_version": dataset["version"],
            **result,
            **objects.add(key, out),
        }
    store.get_scoring_result = lambda rid: scoring_revisions.get(rid)

    calibration_policy = ml_decision.apply_scoring_policy(
        store,
        {
            "scoring_result_id": "calibration",
            "source_owner_actor": "analyst",
            "mode": "quantile",
            "alert_fraction": 0.3,
        },
    )
    policy = {
        "id": "decision-v1",
        "owner_actor": "analyst",
        **calibration_policy,
    }
    store.get_decision_policy_revision = lambda pid: (
        policy if pid == policy["id"] else None
    )
    evaluation = ml_decision.evaluate_scoring_policy(
        store,
        {
            "scoring_result_id": "test",
            "source_owner_actor": "analyst",
            "policy_revision_id": policy["id"],
            "policy_owner_actor": "analyst",
            "reference_owner_actor": "analyst",
        },
    )
    metrics = evaluation["decision_metrics"]
    assert metrics["threshold"] == pytest.approx(policy["threshold"])
    assert metrics["policy_reference_scoring_result_id"] == "calibration"
    assert metrics["temporal_nonoverlap_confirmed"] is True
    assert metrics["independent_validation_confirmed"] is False
    assert evaluation["label_coverage"] == "none"
    assert "precision" not in metrics

    explanation = explain_scored_row(
        store,
        {
            "scoring_result_id": "test",
            "source_owner_actor": "analyst",
            "row_index": 0,
        },
    )
    assert explanation["source_observation"] == {
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "id_column": "observation_uuid",
        "observation_id": original_ids[7],
    }
    assert explanation["model_revision_id"] == model["id"]
    assert explanation["score"] >= 0
