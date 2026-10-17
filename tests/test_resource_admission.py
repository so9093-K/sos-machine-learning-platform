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


def test_worker_budget_is_nested_inside_reserved_envelope():
    """메모리 예약 후 자식이 더 많은 메모리를 요구하는 구성을 사전에 차단한다."""
    from sos_platform.errors import RuntimeConfigurationError

    pool = ExecutionCapacity(pool_mib=3072)
    pool.validate_worker_budget(worker_rss_mib=2048, discovery_memory_mib=1024)
    with pytest.raises(RuntimeConfigurationError, match="SOS_WORKER_MAX_RSS_MIB"):
        pool.validate_worker_budget(worker_rss_mib=2048, discovery_memory_mib=3072)
    with pytest.raises(RuntimeConfigurationError, match="SOS_EXECUTION_POOL_MIB"):
        ExecutionCapacity(pool_mib=1024).validate_worker_budget(
            worker_rss_mib=2048, discovery_memory_mib=512
        )


def test_feature_preview_admission_defaults_and_configuration(monkeypatch):
    from sos_platform.errors import RuntimeConfigurationError
    from sos_platform.resource_admission import FeaturePreviewBudget

    monkeypatch.delenv("SOS_FEATURE_PREVIEW_MEMORY_MIB", raising=False)
    assert FeaturePreviewBudget.from_env().memory_mib == 1024
    monkeypatch.setenv("SOS_FEATURE_PREVIEW_MEMORY_MIB", "512")
    assert FeaturePreviewBudget.from_env().memory_mib == 512
    monkeypatch.setenv("SOS_FEATURE_PREVIEW_MEMORY_MIB", "513")
    with pytest.raises(RuntimeConfigurationError, match="SOS_FEATURE_PREVIEW_MEMORY_MIB"):
        FeaturePreviewBudget.from_env()
