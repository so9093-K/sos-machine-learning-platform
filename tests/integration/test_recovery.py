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


def test_worker_unlock_without_backend_change_revokes_ownership(workspace):
    """동일한 DB Backend PID라도 실제 advisory lock이 없으면 소유권이 없다."""
    from sqlalchemy import text

    from sos_platform.store import WorkerOwnershipLost

    _, store = workspace
    assert store.acquire_worker()
    try:
        assert store._worker_lock.execute(
            text("SELECT pg_advisory_unlock(hashtext('sos-data-worker'))")
        ).scalar_one()
        store._worker_lock.commit()
        with pytest.raises(WorkerOwnershipLost, match="ownership"):
            store._assert_worker_ownership()
    finally:
        store.release_worker()


def test_worker_completion_rolls_back_when_lock_session_dies_mid_transaction(
    workspace, monkeypatch
):
    """Job 결과와 Attempt 업데이트 중 잠금 세션이 죽으면 전체 DB 게시를 롤백한다."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    client, store = workspace
    source = upload(client)
    created = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    assert created.status_code == 202
    assert store.acquire_worker()
    running = store.claim()
    assert running is not None
    original_audit = Store._audit
    killed = False

    def kill_before_commit(db, **kwargs):
        nonlocal killed
        if kwargs["action"] == "job.finish":
            killed = True
            with store.engine.begin() as admin:
                assert admin.execute(
                    text("SELECT pg_terminate_backend(:pid)"),
                    {"pid": store._worker_backend_pid},
                ).scalar_one()
        return original_audit(db, **kwargs)

    monkeypatch.setattr(Store, "_audit", staticmethod(kill_before_commit))
    try:
        with pytest.raises(DBAPIError):
            store.finish(
                running["id"],
                "Succeeded",
                attempt_id=running["attempts"][-1]["id"],
                result={"ok": True},
            )
        assert killed
        pending = store.get_job(running["id"])
        assert pending["status"] == "Running"
        assert pending["attempts"][-1]["status"] == "Running"
        assert pending["result"] is None
    finally:
        store.release_worker()
        monkeypatch.setattr(Store, "_audit", staticmethod(original_audit))
    replacement = Store(store.home, database_url=store.database_url)
    try:
        assert replacement.acquire_worker()
        assert (
            running["id"],
            running["attempts"][-1]["id"],
        ) in replacement.recover()
        assert replacement.get_job(running["id"])["status"] == "Queued"
    finally:
        replacement.release_worker()
        replacement.engine.dispose()


def test_worker_recovery_rolls_back_if_lock_session_dies_during_audit(workspace, monkeypatch):
    """중단 복구가 잠금 세션을 잃으면 Job·Attempt·Audit 변경이 함께 롤백된다."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    client, store = workspace
    source = upload(client)
    created = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    assert created.status_code == 202
    assert store.acquire_worker()
    running = store.claim()
    assert running is not None
    original_audit = Store._audit

    def terminate_before_recovery_commit(db, **kwargs):
        if kwargs["action"] == "job.recover":
            with store.engine.begin() as admin:
                assert admin.execute(
                    text("SELECT pg_terminate_backend(:pid)"),
                    {"pid": store._worker_backend_pid},
                ).scalar_one()
        return original_audit(db, **kwargs)

    monkeypatch.setattr(Store, "_audit", staticmethod(terminate_before_recovery_commit))
    try:
        with pytest.raises(DBAPIError):
            store.recover()
        state = store.get_job(running["id"])
        assert state["status"] == "Running"
        assert state["attempts"][-1]["status"] == "Running"
    finally:
        store.release_worker()
        monkeypatch.setattr(Store, "_audit", staticmethod(original_audit))

    replacement = Store(store.home, database_url=store.database_url)
    try:
        assert replacement.acquire_worker()
        assert (
            running["id"],
            running["attempts"][-1]["id"],
        ) in replacement.recover()
        assert replacement.get_job(running["id"])["status"] == "Queued"
    finally:
        replacement.release_worker()
        replacement.engine.dispose()


def test_recovered_attempt_cannot_create_stale_object_write_intent(workspace):
    """재인수된 Job의 이전 Attempt가 고아 Pending 객체 기록을 만들 수 없다."""
    from sos_platform.store import WorkerOwnershipLost

    client, store = workspace
    source = upload(client)
    created = client.post(
        "/api/jobs",
        json={
            "dataset_id": source["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    )
    assert created.status_code == 202
    first = store.claim()
    old_id = first["attempts"][-1]["id"]
    store.recover()
    current = store.claim()
    current_id = current["attempts"][-1]["id"]
    assert current_id != old_id

    location = ObjectStore().location(f"results/{first['id']}/{old_id}.parquet")
    with pytest.raises(WorkerOwnershipLost, match="attempt"):
        store.begin_object_write(location=location, owner_kind="attempt", owner_id=old_id)
    assert store.object_writes(location) == []

    current_location = ObjectStore().location(f"results/{first['id']}/{current_id}.parquet")
    pending_id = store.begin_object_write(
        location=current_location, owner_kind="attempt", owner_id=current_id
    )
    pending = store.object_writes(current_location)
    assert len(pending) == 1
    assert pending[0]["id"] == pending_id
    assert pending[0]["state"] == "Pending"
    store.recover()
    assert store.object_writes(current_location)[0]["state"] == "Failed"


def test_worker_claim_checks_lock_inside_mutation_transaction(workspace, monkeypatch):
    """Claim 직전 잠금 세션이 종료돼도 새 연결로 Job을 인수하지 않는다."""
    from sqlalchemy import text

    from sos_platform.store import WorkerOwnershipLost

    client, store = workspace
    source = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    assert queued.status_code == 202
    assert store.acquire_worker()
    original_guard = store._assert_worker_ownership

    def terminate_before_ownership_query(*, within_transaction=False):
        # 잠금 검사는 상태 변경 트랜잭션에서 실행되어야 한다.
        assert within_transaction
        assert store._worker_lock.in_transaction()
        with store.engine.begin() as admin:
            assert admin.execute(
                text("SELECT pg_terminate_backend(:pid)"),
                {"pid": store._worker_backend_pid},
            ).scalar_one()
        return original_guard(within_transaction=within_transaction)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, "_assert_worker_ownership", terminate_before_ownership_query)
            with pytest.raises(WorkerOwnershipLost):
                store.claim()
        unchanged = store.get_job(queued.json()["id"])
        assert unchanged["status"] == "Queued"
        assert unchanged["attempts"] == []
    finally:
        store.release_worker()


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


def test_lost_worker_ownership_before_claim_keeps_job_queued(workspace):
    """소유권이 이미 없으면 새 Attempt를 생성하지 않고 Queued를 보존한다."""
    from sos_platform.store import WorkerOwnershipLost

    client, store = workspace
    dataset = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert queued.status_code == 202
    assert store.acquire_worker()

    owned_backend = store._worker_backend_pid
    store._worker_backend_pid = -1
    try:
        with pytest.raises(WorkerOwnershipLost):
            run_once(store)
        unchanged = store.get_job(queued.json()["id"])
        assert unchanged["status"] == "Queued"
        assert unchanged["attempts"] == []
        assert unchanged["result"] is None
    finally:
        store._worker_backend_pid = owned_backend
        store.release_worker()

    replacement = Store(store.home, database_url=store.database_url)
    try:
        assert replacement.acquire_worker()
        assert replacement.recover() == []
        assert replacement.get_job(queued.json()["id"])["status"] == "Queued"
    finally:
        replacement.release_worker()
        replacement.engine.dispose()


def test_lost_worker_ownership_during_execution_leaves_running_for_recovery(workspace, monkeypatch):
    """Claim 이후 소유권을 잃은 실행은 결과를 확정하지 않고 새 Worker 복구에 맡긴다."""
    from sos_platform.store import WorkerOwnershipLost

    client, store = workspace
    dataset = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert queued.status_code == 202
    assert store.acquire_worker()

    owned_backend = store._worker_backend_pid
    original_claim = store.claim

    def claim_then_lose_ownership():
        # 실제 Attempt를 트랜잭션으로 Claim한 직후 소유권 검증값을 변경한다.
        # DB session lock 자체는 보존하므로 이후 복구 경계를 재현할 수 있다.
        claimed = original_claim()
        if claimed is not None:
            store._worker_backend_pid = -1
        return claimed

    monkeypatch.setattr(store, "claim", claim_then_lose_ownership)
    try:
        with pytest.raises(WorkerOwnershipLost):
            run_once(store)
        pending = store.get_job(queued.json()["id"])
        assert pending["status"] == "Running"
        assert pending["attempts"][-1]["status"] == "Running"
        assert pending["result"] is None
    finally:
        store._worker_backend_pid = owned_backend
        store.release_worker()

    replacement = Store(store.home, database_url=store.database_url)
    try:
        assert replacement.acquire_worker()
        interrupted = replacement.recover()
        assert (pending["id"], pending["attempts"][-1]["id"]) in interrupted
        recovered = replacement.get_job(pending["id"])
        assert recovered["status"] == "Queued"
        assert recovered["attempts"][-1]["status"] == "Interrupted"
    finally:
        replacement.release_worker()
        replacement.engine.dispose()
