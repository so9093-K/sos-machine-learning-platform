"""공통 실행 자원 수용 정책.

PostgreSQL 세션 advisory lock으로 API·Worker 간 논리적 메모리를 예약한다.
예약은 실제 RSS의 강제 격리가 아니며, 별도의 실행 한도 감시를 유지한다.
"""

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

from .errors import PlatformError, RuntimeConfigurationError

if TYPE_CHECKING:
    from .store import Store

_QUANTUM_MIB = 256
_LOCK_NAMESPACE = 83420
# 하나의 DB에 여러 API/Worker 인스턴스가 접속할 때 활성 풀 크기를 합의한다.
# 실행 슬롯(0~255)과 별개 키를 사용한다. 동시 시작은 트랜잭션 Mutex로 직렬화.
_POLICY_MUTEX = 512
_POLICY_MARKER_BASE = 1024
_POLICY_MAX_SLOTS = 65536 // _QUANTUM_MIB


@dataclass(frozen=True)
class FeaturePreviewBudget:
    """동기 Polars Preview를 위한 논리적 메모리 요청량. 물리적 강제 한도가 아니다."""

    memory_mib: int = 1024

    @classmethod
    def from_env(cls) -> "FeaturePreviewBudget":
        raw = os.getenv("SOS_FEATURE_PREVIEW_MEMORY_MIB", str(cls().memory_mib))
        if not raw.isdecimal() or not 256 <= int(raw) <= 16384 or int(raw) % _QUANTUM_MIB:
            raise RuntimeConfigurationError(
                "SOS_FEATURE_PREVIEW_MEMORY_MIB must be a multiple of 256 between 256 and 16384"
            )
        return cls(memory_mib=int(raw))


@dataclass(frozen=True)
class ExecutionCapacity:
    """서버의 실물 RAM과 구별되는 논리적 실행 메모리 수용량."""

    pool_mib: int = 3072
    wait_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "ExecutionCapacity":
        defaults = cls()
        capacity = os.getenv("SOS_EXECUTION_POOL_MIB", str(defaults.pool_mib))
        wait = os.getenv("SOS_EXECUTION_POOL_WAIT_SECONDS", str(int(defaults.wait_seconds)))
        if (
            not capacity.isdecimal()
            or not 256 <= int(capacity) <= 65536
            or int(capacity) % _QUANTUM_MIB
        ):
            raise RuntimeConfigurationError(
                "SOS_EXECUTION_POOL_MIB must be a multiple of 256 between 256 and 65536"
            )
        if not wait.isdecimal() or not 1 <= int(wait) <= 300:
            raise RuntimeConfigurationError(
                "SOS_EXECUTION_POOL_WAIT_SECONDS must be between 1 and 300"
            )
        return cls(pool_mib=int(capacity), wait_seconds=float(wait))

    def validate_worker_budget(self, *, worker_rss_mib: int, discovery_memory_mib: int) -> None:
        """실행 자원 요청의 포함 관계를 실행 전에 검사한다.

        부모 Worker가 예약한 예산 안에서 DuckDB가 동작하므로 자식의 DuckDB
        논리 메모리 예산이 부모보다 크면 이중 계상을 피할 수 없다.
        실제 RSS/컨테이너 메모리 한도는 이 계약과 별도로 집행한다.
        """
        if worker_rss_mib <= 0 or discovery_memory_mib <= 0:
            raise RuntimeConfigurationError("Execution memory budgets must be positive")
        if worker_rss_mib > self.pool_mib:
            raise RuntimeConfigurationError(
                "SOS_EXECUTION_POOL_MIB must cover SOS_WORKER_MAX_RSS_MIB"
            )
        if discovery_memory_mib > worker_rss_mib:
            raise RuntimeConfigurationError(
                "SOS_WORKER_MAX_RSS_MIB must cover SOS_DISCOVERY_MEMORY_MIB"
            )

    @property
    def slots(self) -> int:
        return self.pool_mib // _QUANTUM_MIB

    def slots_for(self, requested_mib: int) -> int:
        """요구량은 위로 올림하여 예약하고 총 풀 용량을 절대 초과하지 않는다."""
        if requested_mib <= 0:
            raise ValueError("Execution memory requirement must be positive")
        needed = (requested_mib + _QUANTUM_MIB - 1) // _QUANTUM_MIB
        if needed > self.slots:
            raise RuntimeConfigurationError("Execution memory requirement exceeds configured pool")
        return needed


@contextmanager
def reserve_execution_memory(
    store: "Store", requested_mib: int, policy: ExecutionCapacity | None = None
) -> Iterator[None]:
    """여러 API·Worker 프로세스가 공유하는 예약량을 엄격하게 수용 판단한다.

    예약 경합 시 일부 획득한 슬롯은 즉시 반납한다. 실제 메모리 제한은
    cgroup 또는 Worker 감시 정책에 맡긴다.
    """
    policy = policy or ExecutionCapacity.from_env()
    required = policy.slots_for(requested_mib)
    deadline = time.monotonic() + policy.wait_seconds
    with store.engine.connect() as connection:
        acquired: list[int] = []
        marker = _POLICY_MARKER_BASE + policy.slots
        marker_acquired = False

        def release() -> None:
            while acquired:
                slot = acquired.pop()
                connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                    {"namespace": _LOCK_NAMESPACE, "slot": slot},
                )
                connection.commit()

        try:
            # 잠시만 유지하는 xact Mutex 안에서 타 인스턴스의 활성 정책을 확인하고
            # 세션 공유 Marker를 등록한다. 서로 다른 풀 크기를 동시에 사용하면
            # 작은 풀의 경계를 우회할 수 있으므로 요청 전 거부한다.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, :mutex)"),
                {"namespace": _LOCK_NAMESPACE, "mutex": _POLICY_MUTEX},
            )
            mismatch = connection.execute(
                text(
                    "SELECT 1 FROM pg_locks WHERE locktype='advisory' AND granted "
                    "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                    "AND classid=CAST(:namespace AS oid) AND objsubid=2 "
                    "AND objid BETWEEN CAST(:first AS oid) AND CAST(:last AS oid) "
                    "AND objid<>CAST(:marker AS oid) LIMIT 1"
                ),
                {
                    "namespace": _LOCK_NAMESPACE,
                    "first": _POLICY_MARKER_BASE + 1,
                    "last": _POLICY_MARKER_BASE + _POLICY_MAX_SLOTS,
                    "marker": marker,
                },
            ).first()
            if mismatch:
                raise RuntimeConfigurationError(
                    "SOS_EXECUTION_POOL_MIB differs between active database clients"
                )
            connection.execute(
                text("SELECT pg_advisory_lock_shared(:namespace, :marker)"),
                {"namespace": _LOCK_NAMESPACE, "marker": marker},
            )
            marker_acquired = True
            connection.commit()

            while True:
                for slot in range(policy.slots):
                    granted = connection.execute(
                        text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
                        {"namespace": _LOCK_NAMESPACE, "slot": slot},
                    ).scalar_one()
                    connection.commit()
                    if granted:
                        acquired.append(slot)
                    if len(acquired) == required:
                        break
                if len(acquired) == required:
                    break
                release()
                if time.monotonic() >= deadline:
                    raise PlatformError("SOS-EXECUTION-CAPACITY")
                time.sleep(0.05)
            yield
        finally:
            try:
                release()
            finally:
                # 마지막 실제 슬롯을 반납한 뒤 정책 Marker도 반납한다. 세션이
                # 유실되었다면 PostgreSQL이 두 종류의 advisory lock을 회수한다.
                if marker_acquired:
                    connection.execute(
                        text("SELECT pg_advisory_unlock_shared(:namespace, :marker)"),
                        {"namespace": _LOCK_NAMESPACE, "marker": marker},
                    )
                    connection.commit()
