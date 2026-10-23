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
                "dataset_id TEXT, dataset_version TEXT, owner_actor TEXT,"
                "input_plan TEXT, stage_index INTEGER, job_ids TEXT, status TEXT,"
                "cancel_requested BOOLEAN, error_code TEXT, created_at TEXT, updated_at TEXT)"
            ))
            db.execute(text(
                "CREATE TABLE pipeline_submissions("
                "actor_id TEXT, submission_key TEXT, fingerprint TEXT, run_id TEXT,"
                "PRIMARY KEY(actor_id,submission_key))"
            ))
        self.jobs = {}
        self.next_job = 0
        self.idempotency = {}

    @contextmanager
    def connection(self):
        with self.engine.begin() as db:
            yield db

    def _assert_worker_ownership(self):
        pass

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
        return result

    def request_cancel(self, job_id, *, actor_id):
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
