"""여러 DB 세션에서 API·Worker 논리 메모리 용량 수용과 회수를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock
from time import sleep

import pytest

from sos_platform.errors import PlatformError
from sos_platform.resource_admission import ExecutionCapacity, reserve_execution_memory
from sos_platform.store import Store
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


def test_independent_store_sessions_share_execution_capacity(workspace):
    """서로 다른 API 인스턴스의 PostgreSQL 세션이 같은 용량을 초과 예약하지 않는다."""
    _client, store = workspace
    other = Store(store.home, database_url=store.database_url)
    policy = ExecutionCapacity(pool_mib=512, wait_seconds=0.1)
    acquired = Event()
    release = Event()

    def hold_capacity():
        with reserve_execution_memory(other, 512, policy):
            acquired.set()
            assert release.wait(timeout=5)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_capacity)
            try:
                assert acquired.wait(timeout=3)
                with pytest.raises(PlatformError) as failure:
                    with reserve_execution_memory(store, 256, policy):
                        pytest.fail("Another Store's lock must deny admission")
                assert failure.value.code == "SOS-EXECUTION-CAPACITY"
            finally:
                release.set()
            future.result(timeout=5)
        with reserve_execution_memory(store, 512, policy):
            pass
    finally:
        other.engine.dispose()


def test_partial_reservation_and_exception_release_every_slot(workspace):
    """예약의 부분 획득 실패·실행 예외 모두 다른 요청을 방해하지 않는다."""
    _client, store = workspace
    policy = ExecutionCapacity(pool_mib=512, wait_seconds=0.1)
    with reserve_execution_memory(store, 256, policy):
        with pytest.raises(PlatformError):
            with reserve_execution_memory(store, 512, policy):
                pytest.fail("Insufficient memory")
        # 실패한 512MiB 요청이 잠시 획득한 두 번째 슬롯을 반납했는지 검증한다.
        with reserve_execution_memory(store, 256, policy):
            pass
    with pytest.raises(RuntimeError, match="deliberate failure"):
        with reserve_execution_memory(store, 512, policy):
            raise RuntimeError("deliberate failure")
    with reserve_execution_memory(store, 512, policy):
        pass


def test_parallel_admission_does_not_exceed_logical_capacity(workspace):
    """여러 작업이 동시에 진입해도 논리적인 활성 자원 총량을 초과하지 않는다."""
    _client, store = workspace
    policy = ExecutionCapacity(pool_mib=512, wait_seconds=3)
    meter = Lock()
    active = 0
    peak = 0

    def compute(_index):
        nonlocal active, peak
        with reserve_execution_memory(store, 256, policy):
            with meter:
                active += 1
                peak = max(peak, active)
                assert active <= 2
            try:
                sleep(0.02)
            finally:
                with meter:
                    active -= 1

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(compute, range(12)))
    assert active == 0
    assert 1 <= peak <= 2
    with reserve_execution_memory(store, 512, policy):
        pass


def test_discovery_reserves_memory_before_occupying_concurrency_slot(workspace, monkeypatch):
    """메모리 용량 대기 중인 요청이 실행 슬롯을 먼저 잡지 않도록 한다."""
    import sos_platform.discovery as discovery

    client, store = workspace
    dataset = upload(client)
    lifecycle = []

    @contextmanager
    def memory(*_args, **_kwargs):
        lifecycle.append("reserve_memory")
        try:
            yield
        finally:
            lifecycle.append("release_memory")

    @contextmanager
    def slot(*_args, **_kwargs):
        lifecycle.append("claim_discovery_slot")
        try:
            yield
        finally:
            lifecycle.append("release_discovery_slot")

    monkeypatch.setattr(discovery, "reserve_execution_memory", memory)
    monkeypatch.setattr(discovery, "discovery_slot", slot)
    with discovery.source_connection(store, dataset):
        assert lifecycle == ["reserve_memory", "claim_discovery_slot"]
    assert lifecycle == [
        "reserve_memory",
        "claim_discovery_slot",
        "release_discovery_slot",
        "release_memory",
    ]
