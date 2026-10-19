"""실행 예산의 경계와 실패 코드는 실제 Worker 환경 없이 검증한다."""

import pytest

from sos_platform.execution_budget import ExecutionBudget, ExecutionLimitExceeded


@pytest.fixture
def budget():
    return ExecutionBudget(max_wall_seconds=10, max_rss_mib=256, max_stage_mib=128)


def test_limits_allow_usage_at_boundaries_and_unknown_rss(budget):
    mib = 1024 * 1024
    budget.enforce(elapsed_seconds=10, rss_bytes=256 * mib, stage_bytes=128 * mib)
    # Linux /proc를 제공하지 않는 환경은 엄격한 RSS 제한을 보장하지 못한다.
    budget.enforce(elapsed_seconds=1, rss_bytes=None)


@pytest.mark.parametrize(
    ("elapsed", "rss", "stage", "code", "category"),
    [
        (10.1, 0, None, "SOS-JOB-TIMEOUT", "timeout"),
        (1, 257 * 1024 * 1024, None, "SOS-JOB-MEMORY-LIMIT", "memory"),
        (1, 0, 129 * 1024 * 1024, "SOS-ICEBERG-UNCERTAIN", "storage"),
    ],
)
def test_enforcement_distinguishes_limits(budget, elapsed, rss, stage, code, category):
    with pytest.raises(ExecutionLimitExceeded) as raised:
        budget.enforce(elapsed_seconds=elapsed, rss_bytes=rss, stage_bytes=stage)
    assert raised.value.code == code
    assert raised.value.category == category


def test_budget_checks_timeout_before_commit_uncertainty(budget):
    with pytest.raises(ExecutionLimitExceeded) as raised:
        budget.enforce(
            elapsed_seconds=11, rss_bytes=257 * 1024 * 1024, stage_bytes=129 * 1024 * 1024
        )
    assert raised.value.code == "SOS-JOB-TIMEOUT"


def test_staging_budget_configuration(monkeypatch):
    monkeypatch.setenv("SOS_WORKER_MAX_STAGE_MIB", "512")
    assert ExecutionBudget.from_env().max_stage_mib == 512
    monkeypatch.setenv("SOS_WORKER_MAX_STAGE_MIB", "0")
    with pytest.raises(ValueError, match="SOS_WORKER_MAX_STAGE_MIB"):
        ExecutionBudget.from_env()
