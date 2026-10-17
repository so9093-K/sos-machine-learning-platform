"""PR base와 HEAD의 실제 OpenAPI를 생성하여 후방 호환성을 검증한다."""

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args: list[str], cwd: Path, *, env: dict | None = None) -> str:
    """실패한 명령의 종료 코드를 숨기지 않는다."""
    result = subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return result.stdout


def check(base_ref: str) -> None:
    """의존성 잠금이 다른 base는 독립 환경에서 생성한다."""
    if not re.fullmatch(r"[0-9a-f]{40}", base_ref):
        raise ValueError("--base-ref는 40자리 Git SHA여야 합니다.")
    with tempfile.TemporaryDirectory(prefix="sos-api-base-") as temporary:
        old = Path(temporary) / "base"
        attached = False
        try:
            run(["git", "worktree", "add", "--detach", str(old), base_ref], ROOT)
            attached = True
            old_lock = (old / "uv.lock").read_bytes()
            new_lock = (ROOT / "uv.lock").read_bytes()
            env = dict(os.environ)
            if old_lock == new_lock:
                env["PYTHONPATH"] = str(old / "src")
                before = run(
                    ["uv", "run", "--locked", "python", str(old / "scripts/export_openapi.py")],
                    ROOT,
                    env=env,
                )
            else:
                run(["uv", "sync", "--locked", "--group", "dev"], old)
                before = run(["uv", "run", "--locked", "python", "scripts/export_openapi.py"], old)
            after = run(["uv", "run", "--locked", "python", "scripts/export_openapi.py"], ROOT)
            old_json = Path(temporary) / "before.json"
            new_json = Path(temporary) / "after.json"
            old_json.write_text(before, encoding="utf-8")
            new_json.write_text(after, encoding="utf-8")
            run(
                [
                    "uv",
                    "run",
                    "--locked",
                    "python",
                    "scripts/check_api_compat.py",
                    str(old_json),
                    str(new_json),
                ],
                ROOT,
            )
        finally:
            if attached:
                run(["git", "worktree", "remove", "--force", str(old)], ROOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    args = parser.parse_args()
    check(args.base_ref)


if __name__ == "__main__":
    main()
