"""프로세스 stdout/stderr를 회전 파일에 저장하는 경량 실행 래퍼.

직접 실행한 서비스와 같은 OS 프로세스 그룹에 남아 Make의 그룹 종료를 따른다.
"""

import subprocess
import sys
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3


def capture(
    path: Path, command: list[str], *, max_bytes: int = MAX_BYTES, backups: int = BACKUPS
) -> int:
    if not command or max_bytes < 8192 or not 1 <= backups <= 10:
        raise ValueError("Invalid service log capture settings")
    path.parent.mkdir(parents=True, exist_ok=True)

    def rotate() -> None:
        for index in range(backups, 0, -1):
            old = path if index == 1 else path.with_suffix(path.suffix + f".{index - 1}")
            dest = path.with_suffix(path.suffix + f".{index}")
            if old.exists():
                old.replace(dest)

    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    ) as child:
        assert child.stdout is not None
        size = path.stat().st_size if path.exists() else 0
        output = path.open("ab")
        try:
            while chunk := child.stdout.read(8192):
                if size + len(chunk) > max_bytes:
                    output.close()
                    rotate()
                    output = path.open("ab")
                    size = 0
                output.write(chunk)
                output.flush()
                size += len(chunk)
            return child.wait()
        finally:
            output.close()


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        raise SystemExit("usage: log_capture.py LOG_FILE -- COMMAND...")
    raise SystemExit(capture(Path(sys.argv[1]), sys.argv[3:]))
