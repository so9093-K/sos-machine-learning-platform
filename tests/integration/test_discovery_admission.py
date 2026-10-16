"""여러 DB 세션에서 공통 Discovery 슬롯을 점유·회수한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from sos_platform.discovery import DiscoveryAdmission, discovery_slot
from sos_platform.errors import PlatformError


def test_discovery_slots_are_exclusive_and_released(workspace):
    _client, store = workspace
    started = Event()
    release = Event()
    policy = DiscoveryAdmission(max_active=1, wait_seconds=0.1)

    def hold_slot():
        with discovery_slot(store, policy):
            started.set()
            assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold_slot)
        try:
            assert started.wait(timeout=5)
            with pytest.raises(PlatformError) as failure:
                with discovery_slot(store, policy):
                    pytest.fail("Unexpected parallel Discovery execution")
            assert failure.value.code == "SOS-DISCOVERY-CAPACITY"
        finally:
            release.set()
            future.result(timeout=5)
    with discovery_slot(store, policy):
        pass
