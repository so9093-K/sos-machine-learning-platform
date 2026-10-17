"""실제 Worker 강제 종료 통합 테스트 전용 블로킹 자식 진입점."""

import os
import time
from pathlib import Path

from sos_platform.worker import _guard_child_parent


def paused_execution(
    home: Path, _database_url: str, _job: dict, _completed: Path, _pending: Path, parent_pid: int
) -> None:
    """부모 종료 보호를 장착하고 PID를 기록한 뒤 강제 종료를 기다린다."""
    _guard_child_parent(parent_pid)
    (home / "worker-test-child.pid").write_text(str(os.getpid()), encoding="ascii")
    time.sleep(60)
