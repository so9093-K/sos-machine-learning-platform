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


def test_recovery_reclaims_only_interrupted_attempt_files(workspace):
    """중단된 Attempt의 로컬 결과만 제거하고 무관한 파일은 보존한다."""
    client, store = workspace
    source = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    job = store.claim()
    assert job is not None
    job_id = queued.json()["id"]
    attempt_id = job["attempts"][-1]["id"]
    paths = attempt_result_paths(store, job_id, attempt_id)
    unrelated = store.home / "results" / "unrelated-file.parquet"
    for path in (*paths, unrelated):
        path.write_bytes(b"transient-local-data")

    interrupted = store.recover()
    assert (job_id, attempt_id) in interrupted
    cleanup_recovered_results(store, interrupted)
    assert all(not path.exists() for path in paths)
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


def test_worker_session_loss_does_not_confirm_attempt_or_publish(workspace):
    """실제 DB 백엔드를 종료해도 기존 Worker가 Job 완료를 확정하지 않는다."""
    from sqlalchemy import text

    from sos_platform.store import WorkerOwnershipLost

    client, store = workspace
    source = upload(client)
    response = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    assert response.status_code == 202
    assert store.acquire_worker()
    running = store.claim()
    assert running["id"] == response.json()["id"]
    old_attempt_id = running["attempts"][-1]["id"]
    lost_backend = store._worker_backend_pid
    with store.engine.begin() as conn:
        assert conn.execute(
            text("SELECT pg_terminate_backend(:backend)"),
            {"backend": lost_backend},
        ).scalar_one()
    with pytest.raises(WorkerOwnershipLost):
        store.heartbeat()
    store.release_worker()

    other = Store(store.home, database_url=store.database_url)
    try:
        assert other.acquire_worker()
        interrupted = other.recover()
        assert (running["id"], old_attempt_id) in interrupted
        recovered = other.get_job(running["id"])
        assert recovered["status"] == "Queued"
        assert recovered["attempts"][-1]["status"] == "Interrupted"
        assert recovered["result"] is None
    finally:
        other.release_worker()
        other.engine.dispose()


def test_lost_worker_ownership_during_execution_leaves_running_for_recovery(workspace):
    """잠금 소유권이 변경됐는데 실행 결과가 나와도 이전 Worker는 성공을 게시하지 않는다."""
    from sos_platform.store import WorkerOwnershipLost

    client, store = workspace
    dataset = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert queued.status_code == 202
    assert store.acquire_worker()
    # 실제 세션 종료 테스트와 달리 검증용 식별자만 변경하므로 원본을 보존한다.
    owned_backend = store._worker_backend_pid
    store._worker_backend_pid = -1
    try:
        with pytest.raises(WorkerOwnershipLost):
            run_once(store)
        pending = store.get_job(queued.json()["id"])
        assert pending["status"] == "Running"
        assert pending["attempts"][-1]["status"] == "Running"
        assert pending["result"] is None
    finally:
        # 시뮬레이션 중 실제 PostgreSQL 잠금은 살아 있으므로 정상적으로 해제한다.
        store._worker_backend_pid = owned_backend
        store.release_worker()

    replacement = Store(store.home, database_url=store.database_url)
    try:
        assert replacement.acquire_worker()
        interrupted = replacement.recover()
        assert (pending["id"], pending["attempts"][-1]["id"]) in interrupted
        assert replacement.get_job(pending["id"])["status"] == "Queued"
    finally:
        replacement.release_worker()
        replacement.engine.dispose()
