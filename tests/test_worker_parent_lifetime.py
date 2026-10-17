"""Linux Worker 부모의 SIGKILL 시 고아 자식이 실행을 계속하지 않는지 검증한다."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux-only")
def test_linux_worker_child_dies_after_parent_sigkill(tmp_path: Path):
    """진짜 SIGKILL로 부모를 종료시켜 보호되지 않은 자식의 잔여 실행을 탐지한다."""
    ready = tmp_path / "ready"
    survived = tmp_path / "survived"
    program = """
import os
import signal
import sys
import time
from pathlib import Path
from sos_platform.worker import _guard_child_parent

ready, survived = map(Path, sys.argv[1:3])
parent_pid = os.getpid()
pid = os.fork()
if pid == 0:
    _guard_child_parent(parent_pid)
    temporary = ready.with_name(ready.name + ".tmp")
    temporary.write_text(str(os.getpid()), encoding="ascii")
    temporary.replace(ready)
    time.sleep(0.8)
    survived.write_text("orphan", encoding="ascii")
    os._exit(0)
else:
    deadline = time.monotonic() + 4
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not ready.exists():
        os._exit(2)
    os.kill(os.getpid(), signal.SIGKILL)
"""
    process = subprocess.run(
        [sys.executable, "-c", program, str(ready), str(survived)],
        check=False,
        timeout=7,
    )
    try:
        assert process.returncode == -signal.SIGKILL
        assert ready.exists(), "Child should arm its parent guard before the SIGKILL"
        time.sleep(1)
        assert not survived.exists(), "An orphan continued after its worker was SIGKILLed"
    finally:
        if ready.exists():
            child_pid = int(ready.read_text(encoding="ascii"))
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
