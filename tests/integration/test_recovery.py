"""중단된 Worker 복구 통합 계약을 검증한다."""

import pytest

from sos_platform.object_store import ObjectStore
from sos_platform.store import Store
from sos_platform.worker import (
    attempt_result_paths,
    cleanup_recovered_results,
    run_once,
)

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


def test_recovery_reclaims_only_interrupted_attempt_local_artifacts(workspace):
    """중단된 Attempt와 이전 파일 명명 규칙의 찌꺼기만 제거한다."""
    client, store = workspace
    source = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    job = store.claim()
    assert job is not None
    job_id = queued.json()["id"]
    attempt_id = job["attempts"][-1]["id"]
    paths = attempt_result_paths(store, job_id, attempt_id)
    legacy = store.home / "results" / f"{job_id}.pending.parquet"
    unrelated = store.home / "results" / "unrelated-file.parquet"
    for path in (*paths, legacy, unrelated):
        path.write_bytes(b"transient-local-data")

    interrupted = store.recover()
    assert (job_id, attempt_id) in interrupted
    cleanup_recovered_results(store, interrupted)
    assert all(not path.exists() for path in (*paths, legacy))
    assert unrelated.read_bytes() == b"transient-local-data"
    unrelated.unlink()


def test_new_attempt_uses_distinct_local_paths_after_recovery(workspace):
    """복구 전 Attempt와 다음 시도의 결과·완료 신호 경로가 겹치지 않는다."""
    client, store = workspace
    source = upload(client)
    client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    first = store.claim()
    first_paths = attempt_result_paths(store, first["id"], first["attempts"][-1]["id"])
    store.recover()
    next_attempt = store.claim()
    second_paths = attempt_result_paths(
        store, next_attempt["id"], next_attempt["attempts"][-1]["id"]
    )
    assert set(first_paths).isdisjoint(second_paths)
