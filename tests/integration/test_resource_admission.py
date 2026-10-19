"""여러 DB 세션에서 API·Worker 논리 메모리 용량 수용과 회수를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import sleep

import pytest

from sos_platform.errors import PlatformError
from sos_platform.resource_admission import ExecutionCapacity, reserve_execution_memory
from sos_platform.store import Store
from sos_platform.worker import run_once

from .support import upload


@pytest.mark.pr
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


def test_bad_worker_discovery_memory_envelope_does_not_claim_job(workspace, monkeypatch):
    """설정이 서로 모순되면 부모 예약을 잡거나 Job을 Running으로 만들지 않는다."""
    from sos_platform.errors import RuntimeConfigurationError

    client, store = workspace
    dataset = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert queued.status_code == 202
    monkeypatch.setenv("SOS_DISCOVERY_MEMORY_MIB", "3072")
    with pytest.raises(RuntimeConfigurationError, match="SOS_WORKER_MAX_RSS_MIB"):
        run_once(store)
    assert store.get_job(queued.json()["id"])["status"] == "Queued"
    assert store.get_job(queued.json()["id"])["attempts"] == []


def test_invalid_server_resource_configuration_is_not_client_input_error(workspace, monkeypatch):
    """서버 자원 설정 오류는 안전한 500을 반환하며 422 또는 비밀 설정 원문을 노출하지 않는다."""
    client, store = workspace
    dataset = upload(client)
    monkeypatch.setenv("SOS_DISCOVERY_MEMORY_MIB", "not-a-number-private-credential")
    response = client.get(f"/api/datasets/{dataset['id']}/preview")
    assert response.status_code == 500
    assert response.json()["code"] == "SOS-API-INTERNAL"
    assert "credential" not in response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_feature_preview_does_not_read_data_when_capacity_is_full(workspace, monkeypatch):
    """공통 풀을 모두 사용하면 Polars를 시작하지 않고 동기 429를 반환한다."""
    import sos_platform.api as api

    client, store = workspace
    dataset = upload(client)
    monkeypatch.setenv("SOS_EXECUTION_POOL_WAIT_SECONDS", "1")

    def must_not_process(*_args, **_kwargs):
        raise AssertionError("Feature Preview must not execute without admission")

    monkeypatch.setattr(api, "bounded_feature_preview", must_not_process)
    body = {
        "dataset_id": dataset["id"],
        "transform": {"steps": [{"op": "select", "columns": ["temperature"]}]},
    }
    with reserve_execution_memory(store, 3072):
        result = client.post("/api/features/preview", json=body)
    assert result.status_code == 429
    assert result.json()["code"] == "SOS-EXECUTION-CAPACITY"
    assert result.json()["retry_policy"] == "after_delay"


def test_feature_preview_reservation_is_released_after_internal_failure(workspace, monkeypatch):
    """작업 중 내부 실패가 나더라도 예약 슬롯이 남지 않아 다음 호출이 진행된다."""
    import sos_platform.api as api

    client, store = workspace
    dataset = upload(client)
    body = {
        "dataset_id": dataset["id"],
        "transform": {"steps": [{"op": "select", "columns": ["temperature"]}]},
    }

    def fail_preview(*_args, **_kwargs):
        raise ValueError("private processing detail")

    with monkeypatch.context() as temporary:
        temporary.setattr(api, "bounded_feature_preview", fail_preview)
        broken = client.post("/api/features/preview", json=body)
    assert broken.status_code == 500
    assert broken.json()["code"] == "SOS-API-INTERNAL"
    assert "private processing" not in broken.text
    # 1GiB만 예약할 수 있는 풀에서 정상 호출이 완료된다는 것은 누수가 없다는 뜻이다.
    monkeypatch.setenv("SOS_EXECUTION_POOL_MIB", "1024")
    resumed = client.post("/api/features/preview", json=body)
    assert resumed.status_code == 200, resumed.text
    with reserve_execution_memory(store, 1024):
        pass


def test_active_execution_pool_configuration_mismatch_is_rejected(workspace):
    """서로 다른 인스턴스가 같은 DB에 다른 풀 크기를 주장해 과수용하지 못한다."""
    from sos_platform.errors import RuntimeConfigurationError

    _client, store = workspace
    other = Store(store.home, database_url=store.database_url)
    small = ExecutionCapacity(pool_mib=512, wait_seconds=0.1)
    large = ExecutionCapacity(pool_mib=1024, wait_seconds=0.1)
    try:
        with reserve_execution_memory(store, 256, small):
            with pytest.raises(RuntimeConfigurationError, match="SOS_EXECUTION_POOL_MIB"):
                with reserve_execution_memory(other, 256, large):
                    pytest.fail("Different capacity must not share active leases")
            # 같은 크기인 요청은 정상적으로 동시 수용된다.
            with reserve_execution_memory(other, 256, small):
                pass
        # 활성 작업이 끝나면 다른 크기로의 전환을 방해하지 않는다.
        with reserve_execution_memory(other, 1024, large):
            with pytest.raises(RuntimeConfigurationError, match="SOS_EXECUTION_POOL_MIB"):
                with reserve_execution_memory(store, 256, small):
                    pytest.fail("Old capacity must not silently overlap")
        with reserve_execution_memory(store, 512, small):
            pass
    finally:
        other.engine.dispose()
