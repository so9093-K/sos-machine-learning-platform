"""Worker 프로세스에 적용할 시간·실주민메모리 예산과 관측 도구."""

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import RuntimeConfigurationError


@dataclass(frozen=True)
class ExecutionBudget:
    """운영 환경의 실제 격리를 대체하지 않는 Worker 감시 예산."""

    max_wall_seconds: float = 1800.0
    max_rss_mib: int = 2048
    max_stage_mib: int = 2048

    @classmethod
    def from_env(cls) -> "ExecutionBudget":
        defaults = cls()
        wall = os.getenv("SOS_WORKER_MAX_SECONDS", str(int(defaults.max_wall_seconds)))
        memory = os.getenv("SOS_WORKER_MAX_RSS_MIB", str(defaults.max_rss_mib))
        staging = os.getenv("SOS_WORKER_MAX_STAGE_MIB", str(defaults.max_stage_mib))
        if not wall.isdecimal() or not 1 <= int(wall) <= 86400:
            raise RuntimeConfigurationError("SOS_WORKER_MAX_SECONDS must be between 1 and 86400")
        if not memory.isdecimal() or not 256 <= int(memory) <= 65536:
            raise RuntimeConfigurationError("SOS_WORKER_MAX_RSS_MIB must be between 256 and 65536")
        if not staging.isdecimal() or not 128 <= int(staging) <= 131072:
            raise RuntimeConfigurationError(
                "SOS_WORKER_MAX_STAGE_MIB must be between 128 and 131072"
            )
        return cls(
            max_wall_seconds=float(wall), max_rss_mib=int(memory), max_stage_mib=int(staging)
        )

    def enforce(
        self, *, elapsed_seconds: float, rss_bytes: int | None, stage_bytes: int | None = None
    ) -> None:
        """측정된 사용량을 실행 예산과 비교한다. 물리적인 자원 격리는 별개다."""
        if elapsed_seconds > self.max_wall_seconds:
            raise ExecutionLimitExceeded("SOS-JOB-TIMEOUT", "timeout")
        if stage_bytes is not None and stage_bytes > self.max_stage_mib * 1024 * 1024:
            # Iceberg 커밋 중단 가능성을 부모가 판정할 수 없으므로 보수적으로 실패한다.
            raise ExecutionLimitExceeded("SOS-ICEBERG-UNCERTAIN", "storage")
        if rss_bytes is not None and rss_bytes > self.max_rss_mib * 1024 * 1024:
            raise ExecutionLimitExceeded("SOS-JOB-MEMORY-LIMIT", "memory")


class ExecutionLimitExceeded(Exception):
    """부모 감시자가 자식 프로세스를 중단해야 하는 자원 제한."""

    def __init__(self, code: str, category: str):
        self.code = code
        self.category = category
        super().__init__(code)


def process_rss_bytes(pid: int | None) -> int | None:
    """Linux /proc RSS를 읽으며 미지원 플랫폼에서는 알 수 없음으로 처리한다."""
    if pid is None:
        return None
    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) == 3 and parts[2] == "kB":
                return int(parts[1]) * 1024
    return None
