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

from .errors import PlatformError

if TYPE_CHECKING:
    from .store import Store

_QUANTUM_MIB = 256
_LOCK_NAMESPACE = 83420


@dataclass(frozen=True)
class ExecutionCapacity:
    """서버의 실물 RAM과 구별되는 논리적 실행 메모리 수용량."""

    pool_mib: int = 3072
    wait_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "ExecutionCapacity":
        capacity = os.getenv("SOS_EXECUTION_POOL_MIB", "3072")
        wait = os.getenv("SOS_EXECUTION_POOL_WAIT_SECONDS", "5")
        if (
            not capacity.isdecimal()
            or not 256 <= int(capacity) <= 65536
            or int(capacity) % _QUANTUM_MIB
        ):
            raise ValueError(
                "SOS_EXECUTION_POOL_MIB must be a multiple of 256 between 256 and 65536"
            )
        if not wait.isdecimal() or not 1 <= int(wait) <= 300:
            raise ValueError("SOS_EXECUTION_POOL_WAIT_SECONDS must be between 1 and 300")
        return cls(pool_mib=int(capacity), wait_seconds=float(wait))

    @property
    def slots(self) -> int:
        return self.pool_mib // _QUANTUM_MIB

    def slots_for(self, requested_mib: int) -> int:
        """요구량은 위로 올림하여 예약하고 총 풀 용량을 절대 초과하지 않는다."""
        if requested_mib <= 0:
            raise ValueError("Execution memory requirement must be positive")
        needed = (requested_mib + _QUANTUM_MIB - 1) // _QUANTUM_MIB
        if needed > self.slots:
            raise ValueError("Execution memory requirement exceeds configured pool")
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

        def release() -> None:
            while acquired:
                slot = acquired.pop()
                connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                    {"namespace": _LOCK_NAMESPACE, "slot": slot},
                )
                connection.commit()

        try:
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
            release()
