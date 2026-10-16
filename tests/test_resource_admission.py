"""논리적 실행 용량 초기값·양자화·잘못된 설정을 검증한다."""

import pytest

from sos_platform.resource_admission import ExecutionCapacity


def test_default_capacity_is_a_logical_admission_budget(monkeypatch):
    monkeypatch.delenv("SOS_EXECUTION_POOL_MIB", raising=False)
    monkeypatch.delenv("SOS_EXECUTION_POOL_WAIT_SECONDS", raising=False)
    policy = ExecutionCapacity.from_env()
    assert policy.pool_mib == 3072
    assert policy.wait_seconds == 5
    assert policy.slots == 12
    assert policy.slots_for(256) == 1
    assert policy.slots_for(257) == 2
    assert policy.slots_for(2048) == 8


@pytest.mark.parametrize("value", ["0", "255", "1025", "not-a-number", "65537"])
def test_capacity_configuration_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("SOS_EXECUTION_POOL_MIB", value)
    with pytest.raises(ValueError, match="SOS_EXECUTION_POOL_MIB"):
        ExecutionCapacity.from_env()


def test_capacity_allows_small_environments_with_explicit_resource_budgets(monkeypatch):
    monkeypatch.setenv("SOS_EXECUTION_POOL_MIB", "512")
    monkeypatch.setenv("SOS_EXECUTION_POOL_WAIT_SECONDS", "2")
    policy = ExecutionCapacity.from_env()
    assert policy.slots_for(512) == 2
    with pytest.raises(ValueError, match="exceeds configured pool"):
        policy.slots_for(513)
    with pytest.raises(ValueError, match="must be positive"):
        policy.slots_for(0)
