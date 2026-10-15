"""중단된 Worker 복구 통합 계약을 검증한다."""

import pytest

from sos_platform.object_store import ObjectStore
from sos_platform.store import Store
from sos_platform.worker import run_once

from .support import upload


def test_recovery_marks_interrupted_attempt_writes_failed(workspace):
    """중단된 Attempt의 Pending 기록은 재시도와 구별되는 실패 기록으로 유지한다."""
    client, store = workspace
    dataset = upload(client)
    queued = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    ).json()
    first = store.claim()
    assert first is not None
    attempt_id = first["attempts"][-1]["id"]
    location = ObjectStore().location(f"results/{queued['id']}/{attempt_id}.parquet")
    write_id = store.begin_object_write(
        location=location, owner_kind="attempt", owner_id=attempt_id
    )
    store.recover()
    writes = store.object_writes(location)
    assert len(writes) == 1
    assert writes[0]["id"] == write_id
    assert writes[0]["state"] == "Failed"
    assert run_once(store)
    result = store.get_job(queued["id"])
    assert result["status"] == "Succeeded"
    registered = store.get_dataset(result["result"]["dataset_id"])
    assert registered is not None
    assert registered["location"] != location


def test_recovery_audit_failure_rolls_back_state(workspace, monkeypatch):
    """복구 감사 실패 시 Job·Attempt 상태 전환이 부분 확정되지 않는다."""
    client, store = workspace
    source = upload(client)
    created = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    job = store.claim()
    assert job["id"] == created.json()["id"]
    attempt_id = job["attempts"][-1]["id"]
    original = Store._audit

    def fail_recovery(db, **kwargs):
        if kwargs["action"] == "job.recover":
            raise RuntimeError("Simulated recovery audit outage")
        return original(db, **kwargs)

    monkeypatch.setattr(Store, "_audit", staticmethod(fail_recovery))
    with pytest.raises(RuntimeError, match="recovery audit outage"):
        store.recover()
    assert store.get_job(job["id"])["status"] == "Running"
    assert store.get_job(job["id"])["attempts"][-1]["status"] == "Running"
    monkeypatch.setattr(Store, "_audit", staticmethod(original))
    store.recover()
    assert store.get_job(job["id"])["status"] == "Queued"
    assert store.get_job(job["id"])["attempts"][-1]["status"] == "Interrupted"
    assert any(
        item["action"] == "job.recover"
        and item["resource_id"] == job["id"]
        and item["outcome"] == "requeued"
        and attempt_id in item["reason"]
        for item in store.audit_events(limit=100)
    )
    before = [item for item in store.audit_events(limit=100) if item["action"] == "job.recover"]
    store.recover()
    after = [item for item in store.audit_events(limit=100) if item["action"] == "job.recover"]
    assert after == before
