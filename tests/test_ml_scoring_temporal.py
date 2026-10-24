"""시간순 Calibration/Test 구간 점수화와 Batch 결과 계약을 검증한다."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform import ml_scoring
from sos_platform.schemas import MLScoringRequest


class RecordingScorer:
    def __init__(self):
        self.batch_lengths = []

    def score_anomalies(self, features):
        self.batch_lengths.append(len(features))
        return tuple(float(x[0]) / 1000.0 for x in features)


@pytest.fixture
def scoring_env(monkeypatch, tmp_path):
    model = {
        "id": "model-v1",
        "owner_actor": "researcher",
        "dataset_id": "data-one",
        "dataset_version": "pinned-v1",
        "features": ["count"],
        "runtime": {
            "artifact_schema": "if_model_with_preprocessing_v1",
            "feature_dtypes": ["Float64"],
            "training_selection": {
                "temporal_split": {
                    "timestamp_column": "ts",
                    "train_end": "2026-01-01T04:00:00+00:00",
                    "calibration_end": "2026-01-01T07:00:00+00:00",
                    "train_rows": 4,
                    "calibration_rows": 3,
                    "test_rows": 2,
                }
            },
        },
    }
    dataset = {"id": "data-one", "version": "pinned-v1"}
    rows = pl.DataFrame({
        "ts": [f"2026-01-01T{x:02d}:00:00Z" for x in range(9)],
        "count": [float(x) for x in range(9)],
    })
    frames = [rows]
    @contextmanager
    def scan(_store, _dataset):
        yield frames[0].lazy()

    monkeypatch.setattr(ml_scoring, "pinned_scan_dataset", scan)
    scorer = RecordingScorer()
    monkeypatch.setattr(
        ml_scoring, "load_trusted_isolation_forest",
        lambda _model, _cache: scorer,
    )
    store = SimpleNamespace(home=tmp_path, get_model_revision=lambda _: model)
    return store, model, dataset, scorer, frames


def scope(partition):
    return {
        "partition": partition,
        "timestamp_column": "ts",
        "train_end": "2026-01-01T04:00:00Z",
        "calibration_end": "2026-01-01T07:00:00Z",
    }


def spec(partition=None):
    result = {
        "model_revision_id": "model-v1",
        "model_owner_actor": "researcher",
        "dataset_version": "pinned-v1",
    }
    if partition is not None:
        result["temporal_scope"] = scope(partition)
    return result


def test_time_scoped_calibration_test_results_are_disjoint(scoring_env, tmp_path):
    store, _, dataset, scorer, _ = scoring_env
    calibration_path = tmp_path / "calibration.parquet"
    test_path = tmp_path / "test.parquet"
    cal = ml_scoring.run_dataset_scoring(store, dataset, spec("calibration"), calibration_path)
    tst = ml_scoring.run_dataset_scoring(store, dataset, spec("test"), test_path)
    assert cal["row_count"] == 3
    assert tst["row_count"] == 2
    assert cal["score_summary"]["temporal_scope"]["partition"] == "calibration"
    assert tst["score_summary"]["temporal_scope"]["partition"] == "test"
    assert cal["score_summary"]["training_data_reused"] is False
    assert tst["score_summary"]["training_data_reused"] is False
    assert cal["score_summary"]["temporal_scope"]["dataset_version"] == "pinned-v1"
    assert cal["score_summary"]["temporal_scope"]["calibration_rows"] == 3
    cal_rows = pl.read_parquet(calibration_path)
    test_rows = pl.read_parquet(test_path)
    assert cal_rows["row_index"].to_list() == [0, 1, 2]
    assert test_rows["row_index"].to_list() == [0, 1]
    assert cal_rows["anomaly_score"].to_list() == pytest.approx([0.004, 0.005, 0.006])
    assert test_rows["anomaly_score"].to_list() == pytest.approx([0.007, 0.008])
    assert test_rows["observed_at"].to_list() == [
        "2026-01-01T07:00:00Z", "2026-01-01T08:00:00Z"
    ]
    assert scorer.batch_lengths == [3, 2]
    assert not any(p.name.startswith("ml-score-") for p in tmp_path.iterdir())


def test_scoped_score_retains_uuid4_identity_with_time(scoring_env, tmp_path):
    """서로 다른 입력 범위를 점수화해도 식별자·시각·Feature 행 위치를 보존한다."""
    from uuid import UUID
    store, _, dataset, _, frames = scoring_env
    identities = [str(UUID(int=(1 << 76) | (2 << 62) | i, version=4)) for i in range(9)]
    frames[0] = frames[0].with_columns(
        pl.Series("sos_observation_id", identities)
    )
    requested = spec("test")
    requested["observation_id_column"] = "sos_observation_id"
    path = tmp_path / "identified-test.parquet"
    ml_scoring.run_dataset_scoring(store, dataset, requested, path)
    result = pl.read_parquet(path)
    assert result["observation_id"].to_list() == identities[7:]
    assert result["observed_at"].to_list() == [
        "2026-01-01T07:00:00Z", "2026-01-01T08:00:00Z"
    ]
    assert result["features"].struct.field("count").to_list() == [7.0, 8.0]


def test_time_scoped_datetime_source_preserves_observation_time(scoring_env, tmp_path):
    """타임존이 있는 Datetime 원본도 Scoring 행에 UTC 오프셋을 명확히 보존한다."""
    store, _, dataset, _, frames = scoring_env
    frames[0] = frames[0].with_columns(
        pl.col("ts").str.to_datetime(time_zone="UTC").alias("ts")
    )
    path = tmp_path / "datetime-test.parquet"
    result = ml_scoring.run_dataset_scoring(store, dataset, spec("test"), path)
    assert result["row_count"] == 2
    times = pl.read_parquet(path)["observed_at"].to_list()
    assert all(isinstance(value, str) and value.endswith("+00:00") for value in times)
    assert [datetime.fromisoformat(value) for value in times] == [
        datetime(2026, 1, 1, 7, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
    ]


def test_scoring_requires_same_dataset_revision_and_boundaries(scoring_env, tmp_path):
    store, model, dataset, _, _ = scoring_env
    with pytest.raises(ValueError, match="training Dataset revision"):
        ml_scoring.run_dataset_scoring(store, {**dataset, "id": "another"}, spec("test"),
                                       tmp_path / "wrong.parquet")
    invalid = spec("test")
    invalid["temporal_scope"]["train_end"] = "2026-01-01T03:00:00Z"
    with pytest.raises(ValueError, match="training boundaries"):
        ml_scoring.run_dataset_scoring(store, dataset, invalid, tmp_path / "mismatch.parquet")
    model["runtime"]["training_selection"] = {}
    with pytest.raises(ValueError, match="no pinned"):
        ml_scoring.run_dataset_scoring(store, dataset, spec("test"),
                                       tmp_path / "unsupported.parquet")


def test_scoping_rejects_ambiguous_or_corrupt_timestamps(scoring_env, tmp_path):
    store, _, dataset, _, frames = scoring_env
    bad = frames[0].with_columns(
        pl.when(pl.col("count") == 8)
        .then(pl.lit("2026-01-01T08:00:00"))
        .otherwise(pl.col("ts"))
        .alias("ts")
    )
    frames[0] = bad
    with pytest.raises(ValueError, match="timestamp"):
        ml_scoring.run_dataset_scoring(store, dataset, spec("test"),
                                       tmp_path / "invalid.parquet")


def test_unscoped_batched_scoring_handles_more_than_50000_rows(scoring_env, tmp_path):
    store, _, dataset, scorer, frames = scoring_env
    count = 50_021
    frames[0] = pl.DataFrame({"count": np.arange(count, dtype=np.float64)})
    pending = tmp_path / "batch.parquet"
    result = ml_scoring.run_dataset_scoring(store, dataset, spec(), pending)
    assert result["row_count"] == count
    assert len(scorer.batch_lengths) > 1
    assert max(scorer.batch_lengths) <= ml_scoring.SCORING_BATCH_ROWS
    rows = pl.read_parquet(pending)
    assert rows.height == count
    assert rows["row_index"][0] == 0
    assert rows["row_index"][-1] == count - 1
    assert rows["anomaly_score"][-1] == pytest.approx((count - 1) / 1000)
    assert result["score_summary"]["score_count"] == count
    assert result["score_summary"]["score_mean"] == pytest.approx((count - 1) / 2000)
    assert result["score_summary"]["training_data_reused"] is True


def test_labeled_scoring_keeps_ranking_metrics(scoring_env, tmp_path):
    store, _, dataset, _, frames = scoring_env
    frames[0] = pl.DataFrame({
        "count": [1.0, 2.0, 3.0, 4.0],
        "label": [0, 0, 1, 1],
    })
    request = spec()
    request["label_column"] = "label"
    summary = ml_scoring.run_dataset_scoring(store, dataset, request, tmp_path / "labels.parquet")
    assert summary["ranking_metrics"]["positive_count"] == 2
    assert summary["ranking_metrics"]["roc_auc"] == pytest.approx(1.0)
    assert "label" in pl.read_parquet(tmp_path / "labels.parquet").columns


def test_api_request_disallows_train_partition_and_naive_boundaries():
    MLScoringRequest(model_revision_id="m", dataset_id="d", temporal_scope=scope("test"))
    with pytest.raises(ValidationError):
        MLScoringRequest(model_revision_id="m", dataset_id="d",
                         temporal_scope=scope("train"))
    bad = scope("test")
    bad["train_end"] = "2026-01-01T04:00:00"
    with pytest.raises(ValidationError):
        MLScoringRequest(model_revision_id="m", dataset_id="d", temporal_scope=bad)

 

def test_all_missing_nullable_integer_scoring_chunk_is_valid(scoring_env, monkeypatch, tmp_path):
    """Train에서 결측 대체를 학습한 모델은 전체 null Integer Batch도 처리한다."""
    store, model, dataset, _, frames = scoring_env
    model["runtime"]["feature_dtypes"] = ["Int64"]
    frames[0] = pl.DataFrame({"count": pl.Series([None, None], dtype=pl.Int64)})

    class NullSafeScorer(RecordingScorer):
        def score_anomalies(self, features):
            self.batch_lengths.append(len(features))
            return tuple(float(x) for x in np.nan_to_num(features[:, 0], nan=0.0))

    monkeypatch.setattr(
        ml_scoring, "load_trusted_isolation_forest",
        lambda _model, _cache: NullSafeScorer(),
    )
    result = ml_scoring.run_dataset_scoring(store, dataset, spec(), tmp_path / "emptyints.parquet")
    assert result["row_count"] == 2
    assert pl.read_parquet(tmp_path / "emptyints.parquet")["anomaly_score"].to_list() == [0.0, 0.0]


def test_policy_only_loads_scores_not_full_feature_vectors(scoring_env, monkeypatch, tmp_path):
    """대용량 결과의 Policy/Evaluation은 Feature 구조를 메모리에 적재하지 않는다."""
    import hashlib
    import shutil

    from sos_platform import ml_decision

    store, _, dataset, _, frames = scoring_env
    frames[0] = pl.DataFrame({"count": np.arange(20_021, dtype=np.float64)})
    file = tmp_path / "scores.parquet"
    result = ml_scoring.run_dataset_scoring(store, dataset, spec(), file)
    key = "results/score-job/attempt-1.scores.parquet"

    class LocalStorage:
        def location(self, requested):
            return f"mock://{requested}"

        def get(self, requested, destination, *, expected_sha256):
            assert requested == key
            assert hashlib.sha256(file.read_bytes()).hexdigest() == expected_sha256
            shutil.copyfile(file, destination)

    monkeypatch.setattr(ml_decision, "ObjectStore", lambda: LocalStorage())
    (tmp_path / "cache").mkdir()
    scoring = {
        "id": "scoring-a", "job_id": "score-job", "attempt_id": "attempt-1",
        "location": f"mock://{key}", "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        "row_count": result["row_count"], "owner_actor": "researcher",
    }
    narrow = ml_decision._verified_scoring(store, scoring)
    assert narrow.columns == ["row_index", "anomaly_score"]
    assert narrow.height == 20_021
    full = ml_decision._verified_scoring(store, scoring, include_features=True)
    assert "features" in full.columns
    assert full.height == narrow.height
    row = ml_decision._verified_scoring_row(store, scoring, 20_011)
    assert row["row_index"] == 20_011
    assert row["features"]["count"] == pytest.approx(20_011.0)
    assert row["anomaly_score"] == pytest.approx(20.011)
    with pytest.raises(ValueError, match="out of bounds"):
        ml_decision._verified_scoring_row(store, scoring, 20_021)

    # 특정 행을 읽을 때 전체 Scoring 파일의 pl.read_parquet를 호출하면 회귀다.
    monkeypatch.setattr(
        pl, "read_parquet",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Explanation loaded entire Scoring DataFrame")
        ),
    )
    target = ml_decision._verified_scoring_row(store, scoring, 20_000)
    assert target["row_index"] == 20_000
    scoring["row_count"] += 1
    with pytest.raises(ValueError, match="row count"):
        ml_decision._verified_scoring_row(store, scoring, 20_000)


def test_scoring_stage_is_attempt_scoped_counted_and_cleaned(tmp_path):
    from uuid import uuid4

    from sos_platform.ml_scoring import (
        cleanup_scoring_stage,
        scoring_stage_bytes,
        scoring_stage_prefix,
    )
    from sos_platform.worker import attempt_result_paths, cleanup_recovered_results

    job_id, attempt_id, another_attempt = str(uuid4()), str(uuid4()), str(uuid4())
    root = tmp_path / "results"
    root.mkdir()
    _, pending, _ = attempt_result_paths(
        type("Local", (), {"home": tmp_path})(), job_id, attempt_id
    )
    _, foreign_pending, _ = attempt_result_paths(
        type("Local", (), {"home": tmp_path})(), job_id, another_attempt
    )
    target = pending.with_suffix(".scores.parquet")
    foreign = foreign_pending.with_suffix(".scores.parquet")
    stage = root / f"{scoring_stage_prefix(target)}a"
    foreign_stage = root / f"{scoring_stage_prefix(foreign)}b"
    stage.mkdir()
    foreign_stage.mkdir()
    (stage / "selected.parquet").write_bytes(b"a" * 29)
    (foreign_stage / "selected.parquet").write_bytes(b"b" * 31)
    target.write_bytes(b"c" * 17)
    assert scoring_stage_bytes(tmp_path, target) == 46
    cleanup_recovered_results(
        type("Local", (), {"home": tmp_path})(), [(job_id, attempt_id)]
    )
    assert not stage.exists()
    assert not target.exists()
    assert foreign_stage.is_dir()
    assert scoring_stage_bytes(tmp_path, foreign) == 31
    cleanup_scoring_stage(tmp_path, foreign)
    assert not foreign_stage.exists()


def test_scoring_stage_never_follows_symlinks(tmp_path):
    from sos_platform.ml_scoring import scoring_stage_bytes, scoring_stage_prefix

    root = tmp_path / "results"
    root.mkdir()
    output = root / "test.pending.scores.parquet"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("preserve")
    (root / f"{scoring_stage_prefix(output)}evil").symlink_to(
        outside, target_is_directory=True
    )
    with pytest.raises(ValueError, match="owned directory"):
        scoring_stage_bytes(tmp_path, output)
    assert (outside / "private.txt").read_text() == "preserve"


def test_worker_stops_scoring_stage_overflow_with_distinct_failure_code(
    monkeypatch, tmp_path
):
    from unittest.mock import Mock
    from uuid import uuid4

    from sos_platform import worker
    from sos_platform.execution_budget import ExecutionBudget

    attempt_id, job_id = str(uuid4()), str(uuid4())
    store = Mock()
    store.home = tmp_path
    (tmp_path / "results").mkdir()
    store.claim.return_value = {
        "id": job_id, "kind": "ml_score", "attempts": [{"id": attempt_id}],
    }
    process = Mock()
    process.pid = 1234
    alive = {"value": True}
    process.is_alive.side_effect = lambda: alive["value"]
    process.terminate.side_effect = lambda: alive.update(value=False)
    context = Mock()
    context.Process.return_value = process
    monkeypatch.setattr(worker.multiprocessing, "get_context", lambda _method: context)
    monkeypatch.setattr(worker, "process_rss_bytes", lambda _pid: None)
    monkeypatch.setattr(worker, "scoring_stage_bytes", lambda _home, _output: 2 * 1024 * 1024)
    monkeypatch.setattr(worker, "emit", lambda *_args, **_kwargs: None)
    cgroup = Mock()
    assert worker._run_once_reserved(
        store, ExecutionBudget(max_stage_mib=1), cgroup
    ) is True
    args, kwargs = store.finish.call_args
    assert args == (job_id, "Failed")
    assert kwargs["error_code"] == "SOS-ML-SCORE-STAGE-LIMIT"
    assert kwargs["failure_category"] == "storage"
    process.terminate.assert_called_once()
