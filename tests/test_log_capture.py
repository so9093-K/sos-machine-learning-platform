"""장기 실행 서비스 로그의 유한 크기·안전한 Tail 확인."""

import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "log_capture.py"
DEV_SCRIPT = SCRIPT.parent / "dev.py"
dev_spec = importlib.util.spec_from_file_location("sos_dev_logging", DEV_SCRIPT)
assert dev_spec is not None and dev_spec.loader is not None
dev = importlib.util.module_from_spec(dev_spec)
dev_spec.loader.exec_module(dev)
tail_log = dev.tail_log
spec = importlib.util.spec_from_file_location("sos_log_capture", SCRIPT)
assert spec is not None and spec.loader is not None
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def test_rotated_log_is_bounded_and_keeps_recent_evidence(tmp_path):
    path = tmp_path / "service.log"
    code = "for i in range(10):print('noise-' + 'x'*4000);print('latest-stage=publish')"
    assert capture.capture(path, [sys.executable, "-c", code], max_bytes=8192, backups=2) == 0
    assert path.stat().st_size <= 8192
    assert len(list(tmp_path.glob("service.log.*"))) <= 2
    assert any("latest-stage=publish" in row for row in tail_log(path, max_bytes=8192))


def test_tail_does_not_read_unbounded_history(tmp_path):
    path = tmp_path / "large.log"
    with path.open("wb") as stream:
        for _ in range(1200):
            stream.write(b"historical" + b"x" * 2000 + b"\n")
        stream.write(b"last-valuable-error\n")
    result = tail_log(path, lines=3, max_bytes=8192)
    assert "last-valuable-error" in result[-1]
    assert len(result) <= 3


def test_capture_child_nonzero_exit_is_reported(tmp_path):
    path = tmp_path / "failed.log"
    rc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(path),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(13)",
        ],
        check=False,
        capture_output=True,
    )
    assert rc.returncode == 13
