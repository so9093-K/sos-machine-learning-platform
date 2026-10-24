"""등록형 Pipeline의 단계 계약과 실행 재개를 검사한다."""
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import polars as pl
from sqlalchemy import create_engine, text

from sos_platform import pipeline_reference, pipeline_runs


class FakeStore:
    def __init__(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        with self.engine.begin() as db:
            db.execute(text(
                "CREATE TABLE pipeline_runs("
                "id TEXT PRIMARY KEY, definition_id TEXT, definition_version TEXT,"
                "dataset_id TEXT, dataset_version TEXT, owner_actor TEXT, owner_actor_type TEXT,"
                "input_plan TEXT, stage_index INTEGER, job_ids TEXT, status TEXT,"
                "cancel_requested BOOLEAN, error_code TEXT, created_at TEXT, updated_at TEXT)"
            ))
            db.execute(text(
                "CREATE TABLE pipeline_submissions("
                "actor_id TEXT, submission_key TEXT, fingerprint TEXT, run_id TEXT,"
                "PRIMARY KEY(actor_id,submission_key))"
            ))
        self.jobs = {}
        self.actor_types = {}
        self.cancel_audit = []
        self.next_job = 0
        self.idempotency = {}

    @contextmanager
    def connection(self):
        with self.engine.begin() as db:
            yield db

    def _assert_worker_ownership(self):
        pass

    def get_model_revision(self, model_id):
        return {"id": model_id, "registry_version": "1"}

    def tracking_status(self, job_id):
        return {"state": "Succeeded"}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def create_job(self, *, kind, dataset_id, spec, owner_actor, actor_type, idempotency_key):
        if idempotency_key in self.idempotency:
            return self.jobs[self.idempotency[idempotency_key]]
        self.next_job += 1
        job_id = f"job-{self.next_job}"
        self.idempotency[idempotency_key] = job_id
        outcomes = {
            "ml_pipeline_preflight": {"plan": {
                "timestamp_column": "ts",
                "train_end": "2026-01-01T00:00:00+00:00",
                "calibration_end": "2026-01-02T00:00:00+00:00",
                "partition_sizes": {"train": 3, "calibration": 3, "test": 3},
            }},
            "ml_train": {"model_revision_id": "model-1"},
            "ml_score": {"scoring_result_id": f"score-{self.next_job}"},
            "ml_policy": {"policy_revision_id": "policy-1"},
            "ml_evaluate": {"evaluation_revision_id": "evaluation-1"},
        }
        result = {"id": job_id, "kind": kind, "spec": spec, "status": "Succeeded",
                  "cancel_requested": False, "result": outcomes[kind]}
        self.jobs[job_id] = result
        self.actor_types[job_id] = actor_type
        return result

    def request_cancel(self, job_id, *, actor_id, actor_type="human"):
        self.cancel_audit.append((job_id, actor_id, actor_type))
        self.jobs[job_id]["status"] = "Cancelled"
        return self.jobs[job_id]


def test_pipeline_run_advances_to_test_evaluation():
    store = FakeStore()
    dataset = {"id": "dataset-a", "version": "revision-a"}
    inspection = {"ready": True, "plan": None}
    first = pipeline_runs.create_run(store, dataset, "owner-a", inspection, "key-12345678")
    assert first["status"] == "Queued"
    same = pipeline_runs.create_run(store, dataset, "owner-a", inspection, "key-12345678")
    assert same["id"] == first["id"]
    for _ in range(18):
        if pipeline_runs.get_run(store, first["id"])["status"] == "Succeeded":
            break
        pipeline_runs.advance_run(store)
    finished = pipeline_runs.get_run(store, first["id"])
    assert finished["status"] == "Succeeded"
    assert finished["stage_index"] == len(pipeline_reference.STAGES)
    assert len(finished["stages"]) == len(pipeline_reference.STAGES)
    assert finished["input_plan"]["partition_sizes"]["test"] == 3
    assert list(finished["job_ids"]) == list(pipeline_reference.STAGES)
    assert store.jobs[finished["job_ids"]["test"]]["spec"]["temporal_scope"]["partition"] == "test"
    assert store.jobs[finished["job_ids"]["evaluation"]]["spec"]["policy_revision_id"] == "policy-1"


def test_unknown_definition_version_fails_safely():
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "dataset-version", "version": "version-1"},
        "owner-version", {"ready": True, "plan": None}, None,
    )
    with store.connection() as db:
        db.execute(text(
            "UPDATE pipeline_runs SET definition_version='unknown' WHERE id=:id"
        ), {"id": run["id"]})
    assert pipeline_runs.advance_run(store)
    result = pipeline_runs.get_run(store, run["id"])
    assert result["status"] == "Failed"
    assert store.next_job == 0


def test_cancel_before_claim_does_not_create_ml_jobs():
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "dataset-b", "version": "revision-b"},
        "owner-b", {"ready": True, "plan": None}, None,
    )
    pipeline_runs.cancel_run(store, run["id"])
    assert pipeline_runs.advance_run(store)
    assert pipeline_runs.get_run(store, run["id"])["status"] == "Cancelled"
    assert not store.jobs


def test_schema_check_does_not_scan_full_input(monkeypatch):
    from contextlib import contextmanager
    from datetime import timedelta

    values = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(12)]
    data = {"ts": [value.isoformat() for value in values]}
    data.update({name: [float(i) for i in range(12)] for name in pipeline_reference.FEATURES})
    source = pl.DataFrame(data).lazy()

    @contextmanager
    def fake_scan(_store, _dataset):
        yield source

    monkeypatch.setattr(pipeline_reference, "pinned_scan_dataset", fake_scan)
    dataset = {"id": "one", "version": "two"}
    shallow = pipeline_reference.inspect_dataset(None, dataset)
    assert shallow["ready"] and shallow["plan"] is None
    checked = pipeline_reference.inspect_dataset(None, dataset, full=True)
    assert checked["ready"]
    assert checked["plan"]["partition_sizes"]["train"] >= 2
    assert checked["plan"]["partition_sizes"]["calibration"] >= 1
    assert checked["plan"]["partition_sizes"]["test"] >= 1
    assert sum(checked["plan"]["partition_sizes"].values()) == len(values)


def test_blocked_run_does_not_starve_other_users():
    store = FakeStore()
    first = pipeline_runs.create_run(
        store, {"id": "dataset-1", "version": "v1"}, "user-1",
        {"ready": True, "plan": None}, None,
    )
    assert pipeline_runs.advance_run(store)
    first_preflight_id = pipeline_runs.get_run(store, first["id"])["job_ids"]["preflight"]
    store.jobs[first_preflight_id]["status"] = "Running"
    second = pipeline_runs.create_run(
        store, {"id": "dataset-2", "version": "v2"}, "user-2",
        {"ready": True, "plan": None}, None,
    )
    assert pipeline_runs.advance_run(store)
    actual = pipeline_runs.get_run(store, second["id"])
    assert actual["job_ids"]["preflight"] in store.jobs
    assert pipeline_runs.get_run(store, first["id"])["status"] == "Running"


def test_worker_crash_after_job_commit_reuses_job_on_resume():
    class InterruptedStore(FakeStore):
        interrupted = False

        def create_job(self, **kwargs):
            result = super().create_job(**kwargs)
            if not self.interrupted:
                self.interrupted = True
                raise RuntimeError("crashed before the Run stored the Job ID")
            return result

    import pytest
    store = InterruptedStore()
    run = pipeline_runs.create_run(
        store, {"id": "recovery", "version": "v1"}, "owner-recovery",
        {"ready": True, "plan": None}, None,
    )
    with pytest.raises(RuntimeError, match="crashed"):
        pipeline_runs.advance_run(store)
    assert store.next_job == 1
    assert pipeline_runs.get_run(store, run["id"])["job_ids"] == {}
    assert pipeline_runs.advance_run(store)
    restored = pipeline_runs.get_run(store, run["id"])
    assert restored["job_ids"]["preflight"] == "job-1"
    assert store.next_job == 1


def test_invalid_completed_stage_result_does_not_stop_worker():
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "corrupt-output", "version": "revision"}, "owner",
        {"ready": True, "plan": None}, None,
    )
    assert pipeline_runs.advance_run(store)  # Preflight Job 접수
    preflight = pipeline_runs.get_run(store, run["id"])["job_ids"]["preflight"]
    store.jobs[preflight]["result"] = {}
    assert pipeline_runs.advance_run(store)
    assert pipeline_runs.get_run(store, run["id"])["status"] == "Failed"
    assert store.next_job == 1


def test_service_actor_type_is_preserved_for_pipeline_jobs():
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "service-data", "version": "v1"}, "agent-service",
        {"ready": True, "plan": None}, None, actor_type="service",
    )
    assert pipeline_runs.advance_run(store)
    job_id = pipeline_runs.get_run(store, run["id"])["job_ids"]["preflight"]
    assert store.actor_types[job_id] == "service"


def test_pipeline_cancellation_preserves_human_and_service_actor_types():
    for actor_type in ("human", "service"):
        store = FakeStore()
        owner = f"{actor_type}-owner"
        run = pipeline_runs.create_run(
            store, {"id": "cancel-data", "version": "v1"}, owner,
            {"ready": True, "plan": None}, None, actor_type=actor_type,
        )
        assert pipeline_runs.advance_run(store)
        job_id = pipeline_runs.get_run(store, run["id"])["job_ids"]["preflight"]
        store.jobs[job_id]["status"] = "Running"
        pipeline_runs.cancel_run(store, run["id"])
        assert pipeline_runs.advance_run(store)
        assert store.cancel_audit == [(job_id, owner, actor_type)]
        assert pipeline_runs.advance_run(store)
        assert pipeline_runs.get_run(store, run["id"])["status"] == "Cancelled"


def test_cancel_after_failed_stage_keeps_actual_failure_reason():
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "failed-stage", "version": "v1"}, "owner-failed",
        {"ready": True, "plan": None}, None,
    )
    assert pipeline_runs.advance_run(store)
    job_id = pipeline_runs.get_run(store, run["id"])["job_ids"]["preflight"]
    store.jobs[job_id]["status"] = "Failed"
    store.jobs[job_id]["error_code"] = "SOS-ML-INPUT-INVALID"
    pipeline_runs.cancel_run(store, run["id"])
    assert pipeline_runs.advance_run(store)
    failed = pipeline_runs.get_run(store, run["id"])
    assert failed["status"] == "Failed"
    assert failed["error_code"] == "SOS-ML-INPUT-INVALID"
    assert store.cancel_audit == []


def test_cancel_with_missing_submitted_job_fails_instead_of_hiding_loss():
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "lost-stage", "version": "v1"}, "owner-lost",
        {"ready": True, "plan": None}, None,
    )
    assert pipeline_runs.advance_run(store)
    job_id = pipeline_runs.get_run(store, run["id"])["job_ids"]["preflight"]
    del store.jobs[job_id]
    pipeline_runs.cancel_run(store, run["id"])
    assert pipeline_runs.advance_run(store)
    failed = pipeline_runs.get_run(store, run["id"])
    assert failed["status"] == "Failed"
    assert failed["error_code"] == "SOS-JOB-FAILED"


def test_first_pipeline_example_matches_registered_input_contract(monkeypatch):
    """사용자가 선택하는 IF 예제는 실제 Pipeline 검사와 시간 분할을 통과한다."""
    import io

    from sos_platform.examples import _EXAMPLES, list_examples

    item = next(x for x in list_examples() if x["id"] == "activity-anomaly-if")
    assert item["row_count"] == 240
    assert "ts" in item["columns"]
    csv_data = _EXAMPLES["activity-anomaly-if"]["content"]
    source = pl.read_csv(io.BytesIO(csv_data), infer_schema_length=None).lazy()

    @contextmanager
    def pinned(_store, _dataset):
        yield source

    monkeypatch.setattr(pipeline_reference, "pinned_scan_dataset", pinned)
    dataset = {"id": "selected-if-example", "version": item["version"]}
    check = pipeline_reference.inspect_dataset(None, dataset)
    assert check["ready"] is True
    assert check["feature_count"] == 44
    checked_full = pipeline_reference.inspect_dataset(None, dataset, full=True)
    assert checked_full["ready"] is True, checked_full["issues"]
    assert min(checked_full["plan"]["partition_sizes"].values()) > 0
    assert sum(checked_full["plan"]["partition_sizes"].values()) == item["row_count"]
    assert checked_full["plan"]["observation_id_column"] == "sos_observation_id"



def test_pipeline_stops_when_mlflow_registration_was_never_enabled():
    """학습은 성공했지만 등록 대상이 없다면 무한 대기하지 않는다."""
    store = FakeStore()
    run = pipeline_runs.create_run(
        store, {"id": "dataset-mlflow-missing", "version": "v1"},
        "operator", {"ready": True, "plan": None}, None,
    )
    # preflight 생성 → 결과 기록 → training 생성까지만 전진한다.
    for _ in range(3):
        assert pipeline_runs.advance_run(store)
    result = pipeline_runs.get_run(store, run["id"])
    assert result["job_ids"].get("training")
    store.get_model_revision = lambda _: {"id": "model-1", "registry_version": None}
    store.tracking_status = lambda _: None
    assert pipeline_runs.advance_run(store)
    failed = pipeline_runs.get_run(store, run["id"])
    assert failed["status"] == "Failed"
    assert failed["error_code"] == "SOS-MLFLOW-NOT-CONFIGURED"


def test_pipeline_candidate_evidence_and_legacy_result(monkeypatch, tmp_path):
    """같은 결과 행의 시각·ID를 읽고, 기존 결과의 미존재 근거는 생성하지 않는다."""
    from types import SimpleNamespace

    from sos_platform import api_pipelines

    parquet = tmp_path / "candidate.parquet"
    base = pl.DataFrame({
        "row_index": [0, 1, 2],
        "anomaly_score": [0.2, 0.9, 0.8],
        "features": [{"activity": 2.0}, {"activity": 9.0}, {"activity": 8.0}],
        "observed_at": [
            "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "2026-01-01T02:00:00Z"
        ],
        "observation_id": [
            "18c4be49-18c4-4f32-9894-8c3bfabbd001",
            "18c4be49-18c4-4f32-9894-8c3bfabbd002",
            "18c4be49-18c4-4f32-9894-8c3bfabbd003",
        ],
    })
    base.write_parquet(parquet)

    @contextmanager
    def cached(_store, _entry):
        yield parquet

    monkeypatch.setattr(api_pipelines, "cached_dataset_file", cached)
    store = SimpleNamespace(
        get_scoring_result=lambda _id: {"owner_actor": "owner", "sha256": "digest", "location": "mock"},
        get_decision_policy_revision=lambda _id: {"owner_actor": "owner", "threshold": 0.7},
    )
    run = {
        "owner_actor": "owner",
        "stages": [
            {"name": "test", "job": {"result": {"scoring_result_id": "scoring"}}},
            {"name": "policy", "job": {"result": {"policy_revision_id": "policy"}}},
        ],
    }
    first = api_pipelines._candidate_rows(store, run, 1, None)
    assert first["items"][0] == {
        "row_index": 1, "anomaly_score": 0.9, "features": {"activity": 9.0},
        "observed_at": "2026-01-01T01:00:00Z",
        "observation_id": "18c4be49-18c4-4f32-9894-8c3bfabbd002",
    }
    assert first["next_cursor"] is not None
    selected = api_pipelines._candidate_rows(store, run, 1, None, row_index=2)
    assert selected["items"][0]["row_index"] == 2
    assert selected["next_cursor"] is None
    assert api_pipelines._candidate_rows(store, run, 1, None, row_index=0)["items"] == []
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as invalid:
        api_pipelines._candidate_rows(store, run, 1, first["next_cursor"], row_index=2)
    assert invalid.value.status_code == 422

    base.drop(["observed_at", "observation_id"]).write_parquet(parquet)
    previous = api_pipelines._candidate_rows(store, run, 1, None)
    assert previous["items"][0]["observed_at"] is None
    assert previous["items"][0]["observation_id"] is None
