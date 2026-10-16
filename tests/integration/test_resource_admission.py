"""여러 DB 세션에서 API·Worker 논리 메모리 용량 수용과 회수를 검증한다."""

import pytest

from sos_platform.errors import PlatformError
from sos_platform.resource_admission import ExecutionCapacity, reserve_execution_memory
from sos_platform.worker import run_once

from .support import upload


def test_execution_leases_block_oversubscription_and_release(workspace):
    _client, store = workspace
    policy = ExecutionCapacity(pool_mib=512, wait_seconds=0.1)
    with reserve_execution_memory(store, 256, policy):
        with reserve_execution_memory(store, 256, policy):
            with pytest.raises(PlatformError) as failure:
                with reserve_execution_memory(store, 256, policy):
                    pytest.fail("Oversubscribed logical memory")
            assert failure.value.code == "SOS-EXECUTION-CAPACITY"
    with reserve_execution_memory(store, 512, policy):
        pass


def test_worker_keeps_job_queued_when_execution_pool_is_busy(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    response = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert response.status_code == 202
    monkeypatch.setenv("SOS_EXECUTION_POOL_WAIT_SECONDS", "1")
    with reserve_execution_memory(store, 3072):
        assert run_once(store) is False
    job = store.get_job(response.json()["id"])
    assert job["status"] == "Queued"
    assert job["attempts"] == []


def test_synchronous_discovery_returns_429_when_pool_is_exhausted(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    monkeypatch.setenv("SOS_EXECUTION_POOL_WAIT_SECONDS", "1")
    with reserve_execution_memory(store, 3072):
        response = client.get(f"/api/datasets/{dataset['id']}/preview")
    assert response.status_code == 429
    assert response.json()["code"] == "SOS-EXECUTION-CAPACITY"
    assert response.json()["retry_policy"] == "after_delay"


def test_worker_reserved_memory_is_not_double_counted_by_child_discovery(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    monkeypatch.setenv("SOS_EXECUTION_POOL_MIB", "2048")
    response = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert response.status_code == 202
    assert run_once(store)
    assert store.get_job(response.json()["id"])["status"] == "Succeeded"
