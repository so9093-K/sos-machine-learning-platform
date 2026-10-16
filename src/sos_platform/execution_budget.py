"""Worker 프로세스에 적용할 시간·실주민메모리 예산과 관측 도구."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionBudget:
    """운영 환경의 실제 격리를 대체하지 않는 Worker 감시 예산."""

    max_wall_seconds: float = 1800.0
    max_rss_mib: int = 2048
    max_stage_mib: int = 2048

    @classmethod
    def from_env(cls) -> "ExecutionBudget":
        wall = os.getenv("SOS_WORKER_MAX_SECONDS", "1800")
        memory = os.getenv("SOS_WORKER_MAX_RSS_MIB", "2048")
        staging = os.getenv("SOS_WORKER_MAX_STAGE_MIB", "2048")
        if not wall.isdecimal() or not 1 <= int(wall) <= 86400:
            raise ValueError("SOS_WORKER_MAX_SECONDS must be between 1 and 86400")
        if not memory.isdecimal() or not 256 <= int(memory) <= 65536:
            raise ValueError("SOS_WORKER_MAX_RSS_MIB must be between 256 and 65536")
        if not staging.isdecimal() or not 128 <= int(staging) <= 131072:
            raise ValueError("SOS_WORKER_MAX_STAGE_MIB must be between 128 and 131072")
        return cls(
            max_wall_seconds=float(wall), max_rss_mib=int(memory), max_stage_mib=int(staging)
        )


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
