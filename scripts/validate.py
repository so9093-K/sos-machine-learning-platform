"""제품 검증 명령을 Native Runner에 연결하는 최소 공통 진입점."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 테스트의 수집·실행·성공 판정은 각 Native Runner가 담당한다.
SUITES = {
    "python": ("uv", "run", "--locked", "pytest", "-q", "tests"),
}


def run_suite(name: str) -> int:
    """Runner의 실제 종료 코드를 숨기지 않고 반환한다."""
    command = SUITES[name]
    print(f"[{name}] {' '.join(command)}", flush=True)
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
    except OSError as exc:
        print(f"[{name}] 실행 불가: {exc}", file=sys.stderr)
        return 127
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main() -> int:
    """로컬과 CI가 동일한 이름으로 동일한 검증을 실행한다."""
    parser = argparse.ArgumentParser(description="SOS 제품 검증 실행")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="현재 구성된 검증 표시")
    run = commands.add_parser("run", help="Native Runner로 검증 실행")
    run.add_argument("suite", choices=[*SUITES, "all"])
    args = parser.parse_args()

    if args.command == "list":
        for name in SUITES:
            print(name)
        return 0

    names = SUITES if args.suite == "all" else [args.suite]
    for name in names:
        code = run_suite(name)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
