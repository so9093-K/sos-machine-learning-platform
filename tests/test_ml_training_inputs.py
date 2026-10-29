"""Training 입력 정책·실행 수용 경계와 연구 근거를 확인한다."""

from contextlib import contextmanager

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform import ml_training
from sos_platform.errors import PlatformError
from sos_platform.ml_input import TRAINING_SELECTION_CURRENT, TRAINING_SELECTION_LEGACY
from sos_platform.schemas import MLTrainingInputPolicy, MLTrainRequest


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
    assert earlier["method"] == TRAINING_SELECTION_CURRENT


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


def test_canonical_sample_input_fingerprint_ignores_scan_order(monkeypatch):
    data = pl.DataFrame({"x": np.arange(20_000, dtype=float)})
    bind_frame(monkeypatch, data)
    _, first = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(1000), worker_memory_mib=4
    )
    bind_frame(monkeypatch, data.reverse())
    _, second = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(1000), worker_memory_mib=4
    )
    assert first["selection_sha256"] == second["selection_sha256"]


def test_sample_matches_global_row_priority_reference(monkeypatch):
    count, requested, seed = 5000, 1000, 23
    values = np.arange(count, dtype=np.float64)
    bind_frame(monkeypatch, pl.DataFrame({"x": values}))
    matrix, evidence = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(requested, seed), worker_memory_mib=4,
        selection_algorithm_version=TRAINING_SELECTION_LEGACY
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
             "training_input": sample(400, 9).model_dump(),
             "selection_algorithm_version": TRAINING_SELECTION_CURRENT},
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
    from sos_platform.ml_scoring import SCORING_BATCH_ROWS

    default = MLTrainRequest(dataset_id="d1", features=[f"f_{i}" for i in range(40)])
    assert default.training_input == all_rows()
    assert MLTrainRequest(
        dataset_id="d1", features=["x"], training_input={"mode": "sample", "rows": 100, "seed": 7}
    ).training_input == sample(100, 7)
    assert 0 < SCORING_BATCH_ROWS <= 50_000


def test_http_training_submission_and_fork_preserve_input_policy():
    """API 접수·Fork는 Worker 환경에 따라 입력 정책을 암묵적으로 변경하지 않는다."""
    from unittest.mock import Mock

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from sos_platform.api_ml_training import TRAIN_MODEL_DEP, training_router
    from sos_platform.security import Actor

    store = Mock()
    actor = Actor("alice", "service", frozenset({"automation"}))
    source_job = {
        "id": "original",
        "kind": "ml_train",
        "status": "Succeeded",
        "dataset_id": "d1",
        "spec": {
            "dataset_version": "v1",
            "algorithm": "isolation_forest",
            "features": ["x"],
            "params": {"random_state": 5},
            "training_input": sample(600, 99).model_dump(),
            "selection_algorithm_version": TRAINING_SELECTION_CURRENT,
        },
    }
    def created_job(**kwargs):
        return {
            "id": "job-new",
            "kind": kwargs["kind"],
            "dataset_id": kwargs["dataset_id"],
            "spec": kwargs["spec"],
            "status": "Queued",
            "cancel_requested": False,
            "result": None,
            "error": None,
            "created_at": "2026-10-20T00:00:00Z",
            "updated_at": "2026-10-20T00:00:00Z",
            "attempts": [],
        }

    store.create_job.side_effect = created_job
    app = FastAPI()
    app.include_router(training_router(
        store,
        existing_dataset=lambda dataset_id, _actor: {"id": dataset_id, "version": "v1"},
        existing_job=lambda _job_id, _actor: source_job,
        require_submission_key=lambda key: None,
    ))
    app.dependency_overrides[TRAIN_MODEL_DEP.dependency] = lambda: actor

    @app.middleware("http")
    async def assign_request_id(request: Request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    with TestClient(app) as client:
        reply = client.post("/api/ml/train/jobs", json={
            "dataset_id": "d1",
            "features": ["x"],
            "params": {"random_state": 42},
            "training_input": sample(600, 99).model_dump(),
        })
        assert reply.status_code == 202, reply.text
        assert store.create_job.call_args.kwargs["spec"]["training_input"] == sample(600, 99).model_dump()
        assert store.create_job.call_args.kwargs["spec"]["selection_algorithm_version"] == TRAINING_SELECTION_CURRENT

        invalid = client.post("/api/ml/train/jobs", json={
            "dataset_id": "d1", "features": ["x"],
            "training_input": {"mode": "sample", "rows": 600},
        })
        assert invalid.status_code == 422

        fork = client.post("/api/ml/train/jobs/original/fork", json={})
        assert fork.status_code == 202, fork.text
        assert store.create_job.call_args.kwargs["spec"]["training_input"] == sample(600, 99).model_dump()
        assert store.create_job.call_args.kwargs["spec"]["params"]["random_state"] == 5
        assert store.create_job.call_args.kwargs["spec"]["selection_algorithm_version"] == TRAINING_SELECTION_CURRENT

        # 과거 Job은 정책 필드가 없지만 원본을 전체 사용했다는 의미는 유지한다.
        source_job["spec"].pop("training_input")
        old_fork = client.post("/api/ml/train/jobs/original/fork", json={})
        assert old_fork.status_code == 202, old_fork.text
        assert store.create_job.call_args.kwargs["spec"]["training_input"] == all_rows().model_dump()
        # 구 버전 Job의 선택 알고리즘을 현재 것으로 조용히 변경하지 않는다.
        source_job["spec"].pop("selection_algorithm_version")
        source_job["spec"]["training_input"] = sample(600, 99).model_dump()
        legacy_sample_fork = client.post("/api/ml/train/jobs/original/fork", json={})
        assert legacy_sample_fork.status_code == 202, legacy_sample_fork.text
        assert store.create_job.call_args.kwargs["spec"]["selection_algorithm_version"] == TRAINING_SELECTION_LEGACY


def test_worker_capacity_error_retains_catalog_category():
    from sos_platform.diagnostics import classify_failure

    error = PlatformError("SOS-ML-TRAIN-CAPACITY")
    assert classify_failure(error) == ("capacity", "PlatformError")


def test_full_parquet_training_above_old_limit_creates_model(monkeypatch, tmp_path):
    """50,000행 초과 실제 Parquet에서 모델과 입력 근거까지 확정한다."""
    count = 100_013
    path = tmp_path / "training.parquet"
    values = np.arange(count, dtype=np.float64)
    pl.DataFrame({"x": values, "y": np.sin(values * 0.01)}).write_parquet(path)
    bind_frame(monkeypatch, pl.scan_parquet(path))
    artifact = tmp_path / "training.joblib"
    result = ml_training.train_dataset(
        None,
        {"version": "v1"},
        {
            "algorithm": "isolation_forest",
            "dataset_version": "v1",
            "features": ["x", "y"],
            "params": {"n_estimators": 8, "random_state": 17},
            "training_input": all_rows().model_dump(),
        },
        artifact,
    )
    assert artifact.is_file()
    assert result["source_rows"] == result["training_rows"] == count
    assert result["training_selection"]["selected_rows"] == count
    assert result["training_selection"]["method"] == "all_rows"
    assert result["training_score_summary"]["score_count"] == count
    assert result["runtime"]["training_selection"] == result["training_selection"]


def test_canonical_sample_ignores_input_order_and_chunk_boundaries(monkeypatch):
    # 고유 Feature 값과 중복 관측이 혼합된 Multiset. 순서 및 Chunk의 변화와 무관.
    source = pl.DataFrame({
        "x": np.repeat(np.arange(3300, dtype=np.float64), 3),
        "y": np.tile(np.array([2, 1, 0], dtype=np.int64), 3300),
    })
    policy = sample(900, 27)
    outputs = []
    for frame, memory_mib in (
        (source, 1),
        (source.reverse(), 64),
        (pl.concat([source.slice(3000), source.slice(0, 3000)]), 16),
    ):
        bind_frame(monkeypatch, frame)
        outputs.append(
            ml_training.training_matrix(
                None, {}, ["x", "y"], input_policy=policy, worker_memory_mib=memory_mib
            )
        )
    for matrix, evidence in outputs[1:]:
        np.testing.assert_array_equal(matrix, outputs[0][0])
        assert evidence == outputs[0][1]
    assert outputs[0][1]["algorithm_version"] == TRAINING_SELECTION_CURRENT


def test_canonical_sample_normalizes_signed_zero_and_preserves_duplicates(monkeypatch):
    pos = pl.DataFrame({
        "x": np.array([0.0, -0.0, 0.0, 3.0] * 5000, dtype=np.float32),
        "y": np.array([4, 4, 4, 8] * 5000, dtype=np.int64),
    })
    outputs = []
    for df in (pos, pos.reverse()):
        bind_frame(monkeypatch, df)
        outputs.append(
            ml_training.training_matrix(
                None, {}, ["x", "y"], input_policy=sample(600, 71),
                worker_memory_mib=16,
            )
        )
    np.testing.assert_array_equal(*[item[0] for item in outputs])
    assert outputs[0][1] == outputs[1][1]
    assert not np.signbit(outputs[0][0][:, 0]).any()
    assert 0 < (outputs[0][0][:, 0] == 3.0).sum() < 600


def test_legacy_sample_preserves_previous_row_order_algorithm(monkeypatch):
    numbers = np.arange(1000, dtype=np.float64)[::-1].copy()
    bind_frame(monkeypatch, pl.DataFrame({"x": numbers}))
    matrix, evidence = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(150, 13),
        selection_algorithm_version=TRAINING_SELECTION_LEGACY,
        worker_memory_mib=4,
    )
    priorities = np.random.default_rng(13).integers(
        0, np.iinfo(np.uint64).max, size=len(numbers), dtype=np.uint64
    )
    indices = np.argpartition(priorities, 149)[:150]
    np.testing.assert_array_equal(matrix[:, 0], np.sort(numbers[indices]))
    assert evidence["method"] == TRAINING_SELECTION_LEGACY


def test_unknown_selection_algorithm_rejected(monkeypatch):
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(50.0)}))
    with pytest.raises(ValueError, match="Unsupported training input selection"):
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(10, 8),
            selection_algorithm_version="unrecognized_v99",
        )


def test_canonical_real_parquet_reordered_same_fingerprint(monkeypatch, tmp_path):
    import pyarrow.parquet as pq

    source = pl.DataFrame({
        "x": np.repeat(np.arange(30_000, dtype=np.float64), 2),
        "y": np.tile(np.array([1, 2], dtype=np.int64), 30_000),
    })
    paths = (tmp_path / "ascending.parquet", tmp_path / "descending.parquet")
    for path, frame in zip(paths, (source, source.reverse()), strict=True):
        frame.write_parquet(path)
    outputs = []
    for path in paths:
        assert pq.read_metadata(path).num_rows == source.height
        bind_frame(monkeypatch, pl.scan_parquet(path))
        outputs.append(
            ml_training.training_matrix(
                None, {}, ["x", "y"], input_policy=sample(2000, 5),
                worker_memory_mib=8,
            )
        )
    np.testing.assert_array_equal(outputs[0][0], outputs[1][0])
    assert outputs[0][1] == outputs[1][1]


def test_training_sort_scratch_is_attempt_owned_and_cleaned(monkeypatch, tmp_path):
    from uuid import uuid4

    from sos_platform.ml_training_staging import (
        cleanup_training_stage,
        training_stage_bytes,
        training_stage_path,
    )

    original = str(uuid4())
    second = str(uuid4())
    own = training_stage_path(tmp_path, original)
    other = training_stage_path(tmp_path, second)
    own.mkdir(parents=True)
    (own / "sort").mkdir()
    (own / "sort" / "spill").write_bytes(b"x" * 1234)
    other.mkdir()
    (other / "spill").write_bytes(b"another-attempt")
    assert training_stage_bytes(tmp_path, original) == 1234
    assert training_stage_bytes(tmp_path, second) == len(b"another-attempt")

    cleanup_training_stage(tmp_path, original)
    assert not own.exists()
    assert other.exists()
    cleanup_training_stage(tmp_path)
    assert not other.exists()
    with pytest.raises(ValueError):
        training_stage_path(tmp_path, "../untrusted")


def test_training_stage_cleanup_does_not_follow_unowned_symlinks(tmp_path):
    from uuid import uuid4

    from sos_platform.ml_training_staging import cleanup_training_stage, training_stage_root

    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "important"
    marker.write_text("keep")
    root = training_stage_root(tmp_path)
    root.mkdir(parents=True)
    (root / f"attempt-{uuid4()}").symlink_to(outside, target_is_directory=True)
    cleanup_training_stage(tmp_path)
    assert marker.read_text() == "keep"


def test_pending_legacy_sample_job_keeps_old_selection(monkeypatch, tmp_path):
    # 배포 전에 접수된 Job의 spec에는 새 알고리즘 필드가 없다.
    data = pl.DataFrame({"x": np.arange(400, dtype=float)[::-1].copy()})
    bind_frame(monkeypatch, data)
    _, expected = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(90, 22),
        selection_algorithm_version=TRAINING_SELECTION_LEGACY,
        worker_memory_mib=4,
    )
    result = ml_training.train_dataset(
        None, {"version": "v1"},
        {
            "algorithm": "isolation_forest", "dataset_version": "v1",
            "features": ["x"], "params": {"n_estimators": 8, "random_state": 3},
            "training_input": sample(90, 22).model_dump(),
        },
        tmp_path / "legacy.joblib",
    )
    assert result["training_selection"] == expected
    assert result["runtime"]["training_selection"]["method"] == TRAINING_SELECTION_LEGACY


def test_training_sort_stage_limit_has_distinct_failure_contract():
    from sos_platform.errors import ERRORS

    assert ERRORS["SOS-ML-TRAIN-STAGE-LIMIT"].category == "storage_capacity"
    assert ERRORS["SOS-ML-TRAIN-CAPACITY"].category == "capacity"


def test_worker_child_sets_attempt_scoped_sort_directory(monkeypatch, tmp_path):
    """실제 Worker 자식 진입 경로에서 Polars 정렬 공간이 Attempt에 연결된다."""
    import json
    import os
    from unittest.mock import Mock
    from uuid import uuid4

    from sos_platform import worker
    from sos_platform.ml_training_staging import training_stage_path

    attempt_id = str(uuid4())
    job = {
        "id": str(uuid4()),
        "kind": "ml_train",
        "dataset_id": "dataset",
        "attempts": [{"id": attempt_id}],
        "spec": {"algorithm": "isolation_forest"},
    }
    fake_store = Mock()
    fake_store.home = tmp_path
    fake_store.get_dataset.return_value = {"version": "v1"}
    isolation = Mock()
    monkeypatch.setattr(worker, "_guard_child_parent", lambda _pid: None)
    monkeypatch.setattr(worker, "Store", lambda _home, database_url: fake_store)
    monkeypatch.setattr(worker.CgroupIsolation, "from_env", lambda _limit: isolation)
    expected = training_stage_path(tmp_path, attempt_id)

    def fake_train(store, dataset, spec, path):
        assert store is fake_store
        assert os.environ["POLARS_TEMP_DIR"] == str(expected)
        assert expected.is_dir()
        assert spec == job["spec"]
        path.write_bytes(b"model")
        return {"training_rows": 2}

    monkeypatch.setattr(worker, "train_dataset", fake_train)
    completed = tmp_path / "finished.json"
    worker._perform(
        tmp_path, "postgresql://unused", job, completed, tmp_path / "pending.parquet",
        os.getpid(),
    )
    assert json.loads(completed.read_text()) == {"result": {"training_rows": 2}}
    isolation.attach_current_child.assert_called_once_with(attempt_id)


def test_worker_parent_stops_stage_overflow_and_records_error(monkeypatch, tmp_path):
    """정렬 Spill이 초과되면 부모 감시자가 정상 성공으로 확정하지 않는다."""
    from unittest.mock import Mock
    from uuid import uuid4

    from sos_platform import worker
    from sos_platform.execution_budget import ExecutionBudget

    attempt_id = str(uuid4())
    job_id = str(uuid4())
    store = Mock()
    store.home = tmp_path
    store.claim.return_value = {
        "id": job_id,
        "kind": "ml_train",
        "attempts": [{"id": attempt_id}],
    }
    process = Mock()
    process.pid = 1234
    alive = {"value": True}
    process.is_alive.side_effect = lambda: alive["value"]
    process.terminate.side_effect = lambda: alive.update(value=False)
    process.join.return_value = None
    context = Mock()
    context.Process.return_value = process
    monkeypatch.setattr(worker.multiprocessing, "get_context", lambda _method: context)
    monkeypatch.setattr(worker, "process_rss_bytes", lambda _pid: None)
    monkeypatch.setattr(worker, "training_stage_bytes", lambda _home, _attempt: 2 * 1024 * 1024)
    monkeypatch.setattr(worker, "emit", lambda *_args, **_kwargs: None)
    cgroup = Mock()
    budget = ExecutionBudget(max_stage_mib=1)
    assert worker._run_once_reserved(store, budget, cgroup) is True
    store.finish.assert_called_once()
    args, kwargs = store.finish.call_args
    assert args == (job_id, "Failed")
    assert kwargs["error_code"] == "SOS-ML-TRAIN-STAGE-LIMIT"
    assert kwargs["failure_stage"] == "execution"
    assert kwargs["failure_category"] == "storage"
    process.terminate.assert_called_once()
    cgroup.cleanup.assert_called_once_with(attempt_id)


def test_training_stage_quota_rejects_symlinked_root(tmp_path):
    from uuid import uuid4

    from sos_platform.ml_training_staging import training_stage_bytes, training_stage_root

    outside = tmp_path / "outside"
    outside.mkdir()
    training_stage_root(tmp_path).parent.mkdir(parents=True)
    training_stage_root(tmp_path).symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="must not be a symlink"):
        training_stage_bytes(tmp_path, str(uuid4()))


def test_duckdb_canonical_sample_keeps_independent_duplicate_observations(monkeypatch):
    # 정렬 구현이 중복 관측의 빈도를 변경하지 않는다.
    values = np.array([0.0] * 9000 + [1.0] * 1000)
    outputs = []
    for frame in (pl.DataFrame({"signal": values}), pl.DataFrame({"signal": values[::-1].copy()})):
        bind_frame(monkeypatch, frame)
        outputs.append(ml_training.training_matrix(
            None, {}, ["signal"], input_policy=sample(1800, 29), worker_memory_mib=8
        ))
    np.testing.assert_array_equal(outputs[0][0], outputs[1][0])
    assert outputs[0][1] == outputs[1][1]
    assert 0.84 < np.mean(outputs[0][0][:, 0] == 0.0) < 0.96


def test_duckdb_canonical_rejects_invalid_values_and_cleans_stage(monkeypatch, tmp_path):
    # 잘못된 값이 Sample 밖에 있어도 거부하고 Stage는 정리한다.
    stage = tmp_path / "attempt-stage"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    bind_frame(monkeypatch, pl.DataFrame({"x": [0.0, float("nan"), 2.0]}))
    with pytest.raises(ValueError, match="finite"):
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(2, 7), worker_memory_mib=4
        )
    assert list(stage.iterdir()) == []


def test_duckdb_canonical_same_in_reordered_parquet_layouts(monkeypatch, tmp_path):
    # 서로 다른 물리 배치, Float32, 중복 관측, Signed Zero.
    source = pl.DataFrame({
        "f1": np.repeat(np.arange(2000.0), 3).astype(np.float32),
        "f2": np.tile([0.0, -0.0, 1.0], 2000),
    })
    paths = [tmp_path / "a.parquet", tmp_path / "b.parquet"]
    source.write_parquet(paths[0])
    source.reverse().write_parquet(paths[1])
    outputs = []
    for path in paths:
        bind_frame(monkeypatch, pl.scan_parquet(path))
        outputs.append(ml_training.training_matrix(
            None, {}, ["f1", "f2"], input_policy=sample(500, 11), worker_memory_mib=8
        ))
    np.testing.assert_array_equal(outputs[0][0], outputs[1][0])
    assert outputs[0][1] == outputs[1][1]


def test_duckdb_v1_matches_original_polars_sort_and_seed(monkeypatch, tmp_path):
    import hashlib

    rng = np.random.default_rng(1729)
    values = np.array([0.0, -0.0, 1.5, -1.25, 3.75], dtype=np.float32)
    frame = pl.DataFrame({
        "z": rng.choice(values, size=15_001),
        "x": rng.integers(-300, 300, size=15_001, dtype=np.int64),
        "y": rng.choice([0.0, -0.0, 1.0, 2.0], size=15_001),
    })
    path = tmp_path / "mixed.parquet"
    frame.write_parquet(path)
    bind_frame(monkeypatch, pl.scan_parquet(path))
    rows, seed = 1700, 23
    actual, evidence = ml_training.training_matrix(
        None, {}, ["z", "x", "y"], input_policy=sample(rows, seed), worker_memory_mib=32
    )

    source = frame.with_columns(
        pl.when(pl.col(name) == 0).then(pl.lit(0.0)).otherwise(pl.col(name)).alias(name)
        for name in ("z", "y")
    ).sort(["z", "x", "y"])
    full = np.asarray(source.select(["z", "x", "y"]).to_numpy(), dtype=np.float64)
    priorities = np.random.default_rng(seed).integers(
        0, np.iinfo(np.uint64).max, size=len(full), dtype=np.uint64
    )
    selected = full[np.argpartition(priorities, rows - 1)[:rows]]
    selected = np.ascontiguousarray(selected[np.lexsort(selected.T[::-1])])
    np.testing.assert_array_equal(actual, selected)
    assert evidence["selection_sha256"] == hashlib.sha256(selected.tobytes()).hexdigest()
    assert evidence["algorithm_version"] == TRAINING_SELECTION_CURRENT


def test_duckdb_stage_limit_rejects_large_staged_input(monkeypatch, tmp_path):
    from pathlib import Path

    stage = tmp_path / "attempt-stage"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    # 실제로 허용되는 Stage 최소 예산(128 MiB)을 넘는 파일을 만든다.
    # Sparse File로 디스크·메모리 비용 없이 저장 후 한도 검증만 재현한다.
    monkeypatch.setenv("SOS_WORKER_MAX_STAGE_MIB", "128")
    bind_frame(monkeypatch, pl.DataFrame({"value": np.arange(100.0)}))

    def oversized_sink(_scan, path, **_kwargs):
        with Path(path).open("wb") as target:
            target.truncate(129 * 1024 * 1024)

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", oversized_sink)
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["value"], input_policy=sample(1000, 12),
            worker_memory_mib=256,
        )
    assert exc.value.code == "SOS-ML-TRAIN-STAGE-LIMIT"
    assert list(stage.iterdir()) == []


def test_duckdb_canonical_quotes_feature_names_and_keeps_full_sample(monkeypatch):
    frame = pl.DataFrame({
        'a" b': [3.0, -0.0, 1.0, 2.0],
        "from": [20, 10, 40, 30],
    })
    bind_frame(monkeypatch, frame.reverse())
    matrix, evidence = ml_training.training_matrix(
        None, {}, ['a" b', "from"], input_policy=sample(4, 3), worker_memory_mib=8
    )
    np.testing.assert_array_equal(matrix, np.array([
        [0.0, 10.0], [1.0, 40.0], [2.0, 30.0], [3.0, 20.0],
    ]))
    assert evidence["source_rows"] == 4
    assert evidence["selected_rows"] == 4


def test_canonical_stage_disk_full_is_storage_capacity_and_cleans_stage(monkeypatch, tmp_path):
    import errno
    from pathlib import Path

    stage = tmp_path / "attempt-stage"
    stage.mkdir()
    unrelated = stage / "another-task.txt"
    unrelated.write_text("retain")
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(100.0)}))

    def fail_sink(_self, path, **_kwargs):
        path = Path(path)
        path.write_bytes(b"partly-written")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", fail_sink)
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(10, 17), worker_memory_mib=256
        )
    assert exc.value.code == "SOS-ML-TRAIN-STAGE-LIMIT"
    assert list(stage.iterdir()) == [unrelated]
    assert unrelated.read_text() == "retain"


def test_canonical_duckdb_spill_quota_failure_has_storage_error(monkeypatch, tmp_path):
    import duckdb

    from sos_platform import ml_sampling

    stage = tmp_path / "attempt-stage"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(100.0)}))

    def no_spill(_args=None, **_kwargs):
        raise duckdb.OutOfMemoryException(
            "Out of Memory Error: failed to offload block: maximum temporary directory size"
        )

    monkeypatch.setattr(ml_sampling.duckdb, "connect", no_spill)
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(10, 17), worker_memory_mib=256
        )
    assert exc.value.code == "SOS-ML-TRAIN-STAGE-LIMIT"
    assert list(stage.iterdir()) == []


@pytest.mark.parametrize("seed", [0, 17, 2**32 - 1])
def test_canonical_duckdb_v1_equivalence_at_numeric_precision_boundaries(monkeypatch, seed):
    import hashlib

    i = np.arange(4096) % 13
    floats32 = np.array([-0.0, 0.0, -1.25, 0.375, 42.5], dtype=np.float32)
    floats64 = np.array([-1e300, -1e-300, -0.0, 0.0, 1e-300, 1e300], dtype=np.float64)
    signed = np.array([-(2**53), -(2**53 - 1), -1, 0, 1, 2**53 - 1, 2**53], dtype=np.int64)
    unsigned = np.array([0, 1, 2**53 - 1, 2**53], dtype=np.uint64)
    frame = pl.DataFrame({
        "u64": unsigned[i % len(unsigned)],
        "f32": floats32[i % len(floats32)],
        "i64": signed[i % len(signed)],
        "f64": floats64[i % len(floats64)],
    })
    features = ["u64", "f32", "i64", "f64"]
    rows = 1000
    canonical = frame.with_columns(
        pl.when(pl.col(name) == 0)
        .then(pl.lit(0.0, dtype=frame.schema[name]))
        .otherwise(pl.col(name))
        .alias(name)
        for name in ("f32", "f64")
    ).sort(features).select(features)
    sorted_values = np.asarray(canonical.to_numpy(), dtype=np.float64)
    priorities = np.random.default_rng(seed).integers(
        0, np.iinfo(np.uint64).max, size=len(sorted_values), dtype=np.uint64
    )
    picked = sorted_values[np.argpartition(priorities, rows - 1)[:rows]]
    expected = np.ascontiguousarray(picked[np.lexsort(picked.T[::-1])])
    sha = hashlib.sha256(expected.tobytes()).hexdigest()

    for candidate in (frame, frame.reverse()):
        bind_frame(monkeypatch, candidate)
        actual, evidence = ml_training.training_matrix(
            None, {}, features, input_policy=sample(rows, seed), worker_memory_mib=256
        )
        np.testing.assert_array_equal(actual, expected)
        assert evidence["selection_sha256"] == sha
        assert evidence["method"] == TRAINING_SELECTION_CURRENT


@pytest.mark.parametrize(
    ("exception_name", "message", "expected_code"),
    [
        ("IOException", "No space left on device", "SOS-ML-TRAIN-STAGE-LIMIT"),
        ("IOException", "Disk quota exceeded", "SOS-ML-TRAIN-STAGE-LIMIT"),
        ("OutOfMemoryException", "could not allocate block", "SOS-ML-TRAIN-CAPACITY"),
    ],
)
def test_duckdb_sort_execution_failure_classification_and_cleanup(
    monkeypatch, tmp_path, exception_name, message, expected_code,
):
    import duckdb

    from sos_platform import ml_sampling

    stage = tmp_path / "attempt-stage"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(128.0)}))
    original_connect = ml_sampling.duckdb.connect

    class FailingConnection:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *args, **kwargs):
            if sql.startswith("SELECT "):
                raise getattr(duckdb, exception_name)(message)
            return self.inner.execute(sql, *args, **kwargs)

        def close(self):
            self.inner.close()

    monkeypatch.setattr(
        ml_sampling.duckdb, "connect",
        lambda *args, **kwargs: FailingConnection(original_connect(*args, **kwargs)),
    )
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(12, 8), worker_memory_mib=256
        )
    assert exc.value.code == expected_code
    assert list(stage.iterdir()) == []


@pytest.mark.parametrize(
    ("budget_mib", "sort_memory_mib", "sort_threads"),
    [(512, 128, 1), (2048, 512, 1), (4096, 1024, 2), (8192, 1024, 2)],
)
def test_canonical_sort_budget_scales_without_changing_v1_result(
    monkeypatch, tmp_path, budget_mib, sort_memory_mib, sort_threads,
):
    from sos_platform import ml_sampling

    stage = tmp_path / "attempt"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    monkeypatch.setenv("SOS_WORKER_MAX_STAGE_MIB", "128")
    row_index = np.arange(16384)
    frame = pl.DataFrame({
        "x": (row_index % 197).astype(np.float64),
        "y": (row_index % 13).astype(np.float32),
    })
    bind_frame(monkeypatch, frame.reverse() if budget_mib == 8192 else frame)
    original_connect = ml_sampling.duckdb.connect
    options = []

    def record_connect(*args, **kwargs):
        options.append(kwargs["config"])
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(ml_sampling.duckdb, "connect", record_connect)
    matrix, evidence = ml_training.training_matrix(
        None, {}, ["x", "y"], input_policy=sample(1000, 17),
        worker_memory_mib=budget_mib,
    )
    assert len(options) == 1
    assert options[0]["memory_limit"] == f"{sort_memory_mib}MiB"
    assert options[0]["threads"] == str(sort_threads)
    assert matrix.shape == (1000, 2)
    assert evidence["method"] == TRAINING_SELECTION_CURRENT
    assert not list(stage.iterdir())
    # 동일 Dataset/Seed/K는 실행 자원과 물리적 입력 순서와 무관하다.
    bind_frame(monkeypatch, frame)
    expected, original = ml_training.training_matrix(
        None, {}, ["x", "y"], input_policy=sample(1000, 17),
        worker_memory_mib=2048,
    )
    np.testing.assert_array_equal(matrix, expected)
    assert evidence == original


def test_canonical_selection_is_independent_of_stage_budget_when_execution_fits(
    monkeypatch, tmp_path,
):
    dataset = pl.DataFrame({
        "feature": np.arange(28000, dtype=np.float64) % 311,
        "other": np.arange(28000, dtype=np.float64) % 7,
    })
    bind_frame(monkeypatch, dataset)
    stage = tmp_path / "attempt"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    outputs = []
    for stage_mib in (128, 2048):
        monkeypatch.setenv("SOS_WORKER_MAX_STAGE_MIB", str(stage_mib))
        output = ml_training.training_matrix(
            None, {}, ["feature", "other"], input_policy=sample(2500, 119),
            worker_memory_mib=4096,
        )
        outputs.append(output)
        assert list(stage.iterdir()) == []
    np.testing.assert_array_equal(outputs[0][0], outputs[1][0])
    assert outputs[0][1] == outputs[1][1]


def test_canonical_sort_handles_escaped_stage_path_and_owns_only_its_files(
    monkeypatch, tmp_path,
):
    # DuckDB 설정 문자열에 들어가는 경로는 SQL 리터럴로 이스케이프해야 한다.
    stage = tmp_path / "worker's [training] stage"
    stage.mkdir()
    preserved = stage / "another-attempt.txt"
    preserved.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    source = pl.DataFrame({"x": [3.0, -0.0, 1.0, 2.0]})
    bind_frame(monkeypatch, source.reverse())
    selected, proof = ml_training.training_matrix(
        None, {}, ["x"], input_policy=sample(4, 19), worker_memory_mib=4096
    )
    np.testing.assert_array_equal(selected[:, 0], [0.0, 1.0, 2.0, 3.0])
    assert proof["source_rows"] == 4
    assert list(stage.iterdir()) == [preserved]
    assert preserved.read_text(encoding="utf-8") == "keep"


def test_worker_training_child_completes_actual_duckdb_sampling_and_model(
    monkeypatch, tmp_path,
):
    import json
    import os
    from contextlib import contextmanager
    from unittest.mock import Mock
    from uuid import uuid4

    from sos_platform import worker
    from sos_platform.ml_training_staging import training_stage_path

    source_path = tmp_path / "source.parquet"
    pl.DataFrame({
        "feature": np.arange(180.0, dtype=np.float64) % 41,
        "context": np.arange(180.0, dtype=np.float64) % 11,
    }).write_parquet(source_path)

    @contextmanager
    def scan(_store, _dataset):
        yield pl.scan_parquet(source_path)

    monkeypatch.setattr(ml_training, "pinned_scan_dataset", scan)
    monkeypatch.setattr(worker, "_guard_child_parent", lambda _pid: None)
    store = Mock()
    store.home = tmp_path
    store.get_dataset.return_value = {"version": "revision-a", "kind": "file"}
    monkeypatch.setattr(worker, "Store", lambda _home, database_url: store)
    cgroup = Mock()
    monkeypatch.setattr(worker.CgroupIsolation, "from_env", lambda _limit: cgroup)
    monkeypatch.setenv("SOS_WORKER_MAX_RSS_MIB", "2048")
    monkeypatch.setenv("SOS_WORKER_MAX_STAGE_MIB", "128")
    # 자식 진입점은 env를 설정하므로 테스트에서는 원래 값을 복원한다.
    monkeypatch.setenv("POLARS_TEMP_DIR", str(tmp_path))
    attempt_id = str(uuid4())
    job = {
        "id": str(uuid4()),
        "kind": "ml_train",
        "dataset_id": "dataset",
        "attempts": [{"id": attempt_id}],
        "spec": {
            "algorithm": "isolation_forest",
            "dataset_version": "revision-a",
            "features": ["feature", "context"],
            "params": {"n_estimators": 8, "random_state": 7},
            "training_input": sample(80, 19).model_dump(),
            "selection_algorithm_version": TRAINING_SELECTION_CURRENT,
        },
    }
    pending = tmp_path / "result.pending.parquet"
    completed = tmp_path / "completed.json"
    worker._perform(tmp_path, "postgresql://unused", job, completed, pending, os.getpid())
    outcome = json.loads(completed.read_text(encoding="utf-8"))
    assert "error_code" not in outcome, outcome
    result = outcome["result"]
    assert result["training_rows"] == 80
    assert result["training_selection"]["algorithm_version"] == TRAINING_SELECTION_CURRENT
    assert len(result["training_selection"]["selection_sha256"]) == 64
    assert pending.with_suffix(".joblib").stat().st_size > 0
    assert list(training_stage_path(tmp_path, attempt_id).iterdir()) == []
    cgroup.attach_current_child.assert_called_once_with(attempt_id)


def test_canonical_sort_polars_wrapped_disk_error_preserves_stage_code(
    monkeypatch, tmp_path,
):
    # Polars의 모든 스토리지 실패가 ComputeError로 전달된다고 가정하지 않는다.
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(256.0)}))

    def fail_sink(_self, output, **_options):
        from pathlib import Path

        Path(output).write_bytes(b"partial")
        raise pl.exceptions.InvalidOperationError("No space left on device")

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", fail_sink)
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(32, 9), worker_memory_mib=2048
        )
    assert exc.value.code == "SOS-ML-TRAIN-STAGE-LIMIT"
    assert not list(stage.iterdir())


def test_canonical_sort_disk_quota_exhaustion_is_stage_limit(monkeypatch, tmp_path):
    import errno

    stage = tmp_path / "attempt"
    stage.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(stage))
    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(128.0)}))

    def fail_sink(_source, _path, **_kwargs):
        raise OSError(errno.EDQUOT, "Disk quota exceeded")

    monkeypatch.setattr(pl.LazyFrame, "sink_parquet", fail_sink)
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(50, 9), worker_memory_mib=2048
        )
    assert exc.value.code == "SOS-ML-TRAIN-STAGE-LIMIT"
    assert list(stage.iterdir()) == []


def test_canonical_sort_temp_directory_creation_disk_full_is_stage_limit(
    monkeypatch,
):
    import errno

    from sos_platform import ml_sampling

    bind_frame(monkeypatch, pl.DataFrame({"x": np.arange(128.0)}))

    def unavailable_stage(**_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ml_sampling, "TemporaryDirectory", unavailable_stage)
    with pytest.raises(PlatformError) as exc:
        ml_training.training_matrix(
            None, {}, ["x"], input_policy=sample(40, 9), worker_memory_mib=2048
        )
    assert exc.value.code == "SOS-ML-TRAIN-STAGE-LIMIT"


def test_staged_all_selection_preserves_full_sorted_matrix_and_hash(monkeypatch, tmp_path):
    """입력 의미는 전과 같고 물리적 저장소만 Memmap으로 바뀐다."""
    data = pl.DataFrame({
        "a": [4.0, 2.0, None, 2.0, 10.0, 0.0],
        "b": [9.0, 3.0, 7.0, 3.0, 8.0, 2.0],
    })
    bind_frame(monkeypatch, data)
    legacy, earlier = ml_training.training_matrix(
        None, {}, ["a", "b"], input_policy=all_rows(),
        missing_policy="train_median", worker_memory_mib=64,
    )
    scratch = tmp_path / "training-stage"
    scratch.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(scratch))
    with ml_training.staged_all_training_matrix(
        None, {}, ["a", "b"], missing_policy="train_median", temporal_split=None
    ) as (staged, selection):
        assert isinstance(staged, np.memmap)
        np.testing.assert_array_equal(staged, legacy)
        assert selection == earlier
        assert selection["selected_rows"] == data.height
        assert any(scratch.iterdir())
    assert not list(scratch.iterdir())


def test_all_model_training_no_longer_uses_sample_budget_as_row_cap(monkeypatch, tmp_path):
    """Full 모델 학습은 Sample 배열의 보수적 사전 상한을 사용하지 않는다."""
    count = 20_001
    bind_frame(monkeypatch, pl.DataFrame({
        "value": np.linspace(0.0, 10.0, count),
        "secondary": np.linspace(4.0, 0.0, count),
    }))
    scratch = tmp_path / "worker-attempt-stage"
    scratch.mkdir()
    monkeypatch.setenv("POLARS_TEMP_DIR", str(scratch))
    # 기존 training_matrix의 1 MiB RSS 예산에서는 이 모든 행이 거절된다.
    with pytest.raises(PlatformError):
        ml_training.training_matrix(
            None, {}, ["value", "secondary"],
            input_policy=all_rows(), worker_memory_mib=1,
        )
    # 실질적인 all 모델 학습은 입력을 줄이지 않고 Stage-backed 계약으로 실행한다.
    artifact = tmp_path / "full.joblib"
    outcome = ml_training.train_dataset(
        None, {"version": "v1"},
        {
            "algorithm": "isolation_forest",
            "dataset_version": "v1",
            "features": ["value", "secondary"],
            "params": {"n_estimators": 8, "random_state": 7},
            "training_input": {"mode": "all"},
            "missing_policy": "reject",
        },
        artifact,
    )
    assert outcome["training_rows"] == outcome["source_rows"] == count
    assert outcome["training_selection"]["selected_rows"] == count
    assert outcome["training_score_summary"]["score_count"] == count
    assert artifact.is_file()
    assert not list(scratch.iterdir())


def test_all_model_and_sample_training_have_separate_selection_modes(monkeypatch, tmp_path):
    """Full 모드가 의도치 않게 Sample 입력으로 전환되지 않는지 확인한다."""
    count = 8_141
    bind_frame(monkeypatch, pl.DataFrame({
        "x": np.arange(count, dtype=np.float64),
        "y": [None if i % 11 == 0 else float(i) for i in range(count)],
    }))
    raw_spec = {
        "algorithm": "isolation_forest",
        "dataset_version": "v1",
        "features": ["x", "y"],
        "params": {"n_estimators": 8, "random_state": 7},
        "missing_policy": "train_median",
    }
    full = ml_training.train_dataset(
        None, {"version": "v1"}, {
            **raw_spec, "training_input": {"mode": "all"}
        }, tmp_path / "full.joblib"
    )
    sampled = ml_training.train_dataset(
        None, {"version": "v1"}, {
            **raw_spec, "training_input": {"mode": "sample", "rows": 200, "seed": 17},
        }, tmp_path / "sample.joblib"
    )
    assert full["training_rows"] == count
    assert sampled["training_rows"] == 200
    assert full["training_selection"]["method"] == "all_rows"
    assert sampled["training_selection"]["method"] != "all_rows"
    assert full["runtime"]["feature_reference_medians"] != sampled["runtime"]["feature_reference_medians"]
