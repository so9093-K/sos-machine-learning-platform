"""pytest·Vitest·Playwright Native Collector에서 테스트 자산 목록을 생성한다.

테스트 구현의 파일 목록을 복제하지 않으며 수집 0건을 성공으로 취급하지 않는다.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def command(args: list[str], cwd: Path) -> str:
    """수집 도구의 실패 종료 코드를 그대로 전달한다."""
    result = subprocess.run(
        args,
        cwd=cwd,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def python_ids(output: str) -> list[str]:
    """pytest --collect-only에서 실제 nodeid를 수집한다."""
    return sorted(
        {
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith("tests/") and "::" in line
        }
    )


def vitest_ids(value: object) -> list[str]:
    """Vitest의 네이티브 JSON 목록에서 Test ID를 보존한다."""
    ids = []

    def walk(item: object, scope: str = "") -> None:
        if isinstance(item, list):
            for entry in item:
                walk(entry, scope)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title")
            filename = item.get("file") or item.get("filepath") or scope
            if isinstance(filename, dict):
                filename = filename.get("filepath") or filename.get("name") or scope
            children = [
                item.get(key)
                for key in ("tasks", "children", "tests", "files", "items", "testCases")
                if item.get(key) is not None
            ]
            if children:
                for child in children:
                    walk(child, str(filename))
            elif isinstance(name, str) and name:
                ids.append(f"{filename}::{name}")
            elif isinstance(item.get("id"), str):
                ids.append(item["id"])

    walk(value)
    return sorted(set(ids))


def playwright_ids(output: str) -> list[str]:
    """Playwright --list가 제공한 테스트별 파일/행/이름을 보존한다."""
    result = set()
    for line in output.splitlines():
        line = line.strip()
        if ".spec.ts:" not in line or "›" not in line:
            continue
        parts = [part.strip() for part in line.split("›")]
        if len(parts) >= 3:
            result.add("::".join(parts[1:]))
    return sorted(result)


def collect() -> list[dict]:
    """Native Runner 결과가 비어 있으면 인벤토리도 성공으로 만들지 않는다."""
    python = python_ids(command(["uv", "run", "--locked", "pytest", "--collect-only", "-q"], ROOT))
    vitest = vitest_ids(
        json.loads(command(["npx", "vitest", "list", "--json"], ROOT / "web"))
    )
    playwright = playwright_ids(command(["npx", "playwright", "test", "--list"], ROOT / "web"))
    groups = {"pytest": python, "vitest": vitest, "playwright": playwright}
    if any(not ids for ids in groups.values()):
        raise ValueError("필수 Native Runner 중 테스트 수집 0건")
    records = []
    for runner, ids in groups.items():
        for identifier in ids:
            records.append(
                {
                    "runner": runner,
                    "test_id": identifier,
                    "layer": (
                        "integration"
                        if runner == "pytest" and identifier.startswith("tests/integration/")
                        else ("browser" if runner == "playwright" else "unit")
                    ),
                    "contract": "review_required",
                    "risk": "review_required",
                    "lifecycle": "active",
                }
            )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        records = collect()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"schema_version": 1, "tests": records}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"수집 완료: {len(records)}개 (수명주기 검토 필요)")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"테스트 인벤토리 수집 실패: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
