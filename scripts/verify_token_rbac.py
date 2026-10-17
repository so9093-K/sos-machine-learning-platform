"""기존 권한 시나리오를 외부·개발 데이터에 접근하지 않고 실행한다."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    subprocess.run(
        [
            "python3",
            "scripts/run_isolated_e2e.py",
            "--spec",
            "auth.spec.ts",
            "--token-mode",
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
