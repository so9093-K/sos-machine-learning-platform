"""테스트 자산 인벤토리·보호 계약·실행 증거의 Native Runner 대조.

테스트 정의의 정본은 pytest·Vitest·Playwright이며 Registry는 보호 의미만 저장한다.
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT / "tests" / "contracts.json"
RUNNERS = ("pytest", "vitest", "playwright")
RISKS = ("normal", "high", "critical")
LIFECYCLES = ("active", "review_required")


def command(args: list[str], cwd: Path) -> str:
    """Native Runner 오류를 비밀값 없이 식별하고 실패를 보존한다."""
    result = subprocess.run(
        args,
        cwd=cwd,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        name = args[1] if args[0] == "npx" and len(args) > 1 else args[0]
        print(f"Native 수집 실패: {name}, exit={result.returncode}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, args)
    return result.stdout


def python_ids(output: str) -> list[str]:
    """pytest Node ID를 보존하며 요약·경고 메시지는 포함하지 않는다."""
    return sorted(
        {
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith("tests/") and "::" in line
        }
    )


def vitest_ids(value: object) -> list[str]:
    """Vitest의 native JSON 구조를 순회하고 테스트별 제목을 보존한다."""
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
    """최상위·중첩 Playwright 테스트를 파일과 안정적 제목으로 모두 수집한다."""
    result = set()
    for line in output.splitlines():
        match = re.search(
            r"(?P<file>[\w./-]+\.spec\.ts):\d+:\d+\s*›\s*(?P<title>.+)$",
            line.strip(),
        )
        if not match:
            continue
        filename = Path(match.group("file")).name
        names = [part.strip() for part in match.group("title").split("›")]
        if not all(names):
            raise ValueError("Playwright 수집 행의 테스트명이 비어 있습니다.")
        result.add(f"web/e2e/{filename}::{'::'.join(names)}")
    return sorted(result)


def source_path(runner: str, test_id: str) -> str:
    """Native Runner별 경로 표현을 저장소 상대 경로로 정규화한다."""
    source = test_id.split("::", 1)[0].replace("\\", "/")
    if runner == "pytest":
        if not source.startswith("tests/"):
            raise ValueError(f"pytest 출처가 tests/가 아닙니다: {source}")
        return source
    if runner == "vitest":
        if "/web/" in source:
            return "web/" + source.rsplit("/web/", 1)[1]
        if source.startswith("web/"):
            return source
        if source.startswith("src/"):
            return "web/" + source
        raise ValueError("Vitest 출처가 web/src 내부가 아닙니다.")
    if runner == "playwright":
        if source.startswith("web/e2e/"):
            return source
        if source.endswith(".spec.ts"):
            return "web/e2e/" + Path(source).name
        raise ValueError("Playwright 출처가 e2e 테스트가 아닙니다.")
    raise ValueError(f"미지원 Native Runner: {runner}")


def test_title(test_id: str) -> str:
    """변동 가능한 Playwright 행 번호 대신 테스트 제목을 사용한다."""
    return test_id.split("::", 1)[1] if "::" in test_id else ""


def stable_key(runner: str, identifier: str) -> str:
    """JUnit과 수집 결과가 공유하는 파일·테스트명 기반 ID."""
    title = test_title(identifier)
    title = re.sub(r"\s*(?:›|>)\s*", "::", title)
    title = "::".join(part.strip() for part in title.split("::"))
    return f"{source_path(runner, identifier)}::{title}"


def load_registry(path: Path) -> list[dict]:
    """Registry의 스키마·ID·선택자를 검증하고 잘못된 정책을 거부한다."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("지원되지 않는 계약 Registry 버전")
    contracts = value.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("보호 계약 목록이 없습니다.")
    seen = set()
    for contract in contracts:
        if not isinstance(contract, dict):
            raise ValueError("보호 계약 형식 오류")
        identifier = contract.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Z0-9]+(?:-[A-Z0-9]+)+", identifier
        ):
            raise ValueError("안정적인 Contract ID가 필요합니다.")
        if identifier in seen:
            raise ValueError(f"중복 Contract ID: {identifier}")
        seen.add(identifier)
        if contract.get("risk") not in RISKS:
            raise ValueError(f"보호 계약 위험도 오류: {identifier}")
        if not isinstance(contract.get("description"), str) or not contract["description"]:
            raise ValueError(f"보호 계약 설명 누락: {identifier}")
        selectors = contract.get("selectors")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"보호 계약 연결 선택자 누락: {identifier}")
        for selector in selectors:
            if not isinstance(selector, dict) or selector.get("runner") not in RUNNERS:
                raise ValueError(f"보호 계약 Runner 오류: {identifier}")
            pattern = selector.get("path_glob")
            if not isinstance(pattern, str) or not pattern.startswith(("tests/", "web/")):
                raise ValueError(f"보호 계약 경로 오류: {identifier}")
            title = selector.get("title_glob", "*")
            if not isinstance(title, str) or not title:
                raise ValueError(f"보호 계약 제목 조건 오류: {identifier}")
    return contracts


def classify(groups: dict[str, list[str]], registry: list[dict]) -> list[dict]:
    """Native 테스트 ID에 다대다 보호 계약을 연결하고 미분류는 리뷰 대상으로 남긴다."""
    records = []
    matched = Counter()
    for runner, items in groups.items():
        if not items:
            raise ValueError(f"Native Runner 테스트 수집 0건: {runner}")
        for identifier in items:
            path = source_path(runner, identifier)
            name = test_title(identifier)
            links = []
            for contract in registry:
                for index, selector in enumerate(contract["selectors"]):
                    if runner != selector["runner"]:
                        continue
                    if fnmatch.fnmatchcase(path, selector["path_glob"]) and fnmatch.fnmatchcase(
                        name, selector.get("title_glob", "*")
                    ):
                        links.append(contract["id"])
                        matched[(contract["id"], index)] += 1
                        break
            links.sort()
            risk = (
                max(
                    (contract["risk"] for contract in registry if contract["id"] in links),
                    key=RISKS.index,
                )
                if links
                else "review_required"
            )
            records.append(
                {
                    "runner": runner,
                    "test_id": identifier,
                    "source_path": path,
                    "layer": (
                        "integration"
                        if runner == "pytest" and path.startswith("tests/integration/")
                        else ("browser" if runner == "playwright" else "unit")
                    ),
                    "contract_ids": links,
                    "risk": risk,
                    "lifecycle": "active" if links else "review_required",
                }
            )
    for contract in registry:
        for index, _ in enumerate(contract["selectors"]):
            if matched[(contract["id"], index)] == 0:
                raise ValueError(f"보호 계약 선택자 미수집: {contract['id']} #{index + 1}")
    return records


def collect() -> dict[str, list[str]]:
    """Native Runner의 실제 수집 출력을 정본으로 반환한다."""
    return {
        "pytest": python_ids(
            command(["uv", "run", "--locked", "pytest", "--collect-only", "-q"], ROOT)
        ),
        "vitest": vitest_ids(
            json.loads(command(["npx", "vitest", "list", "src", "--json"], ROOT / "web"))
        ),
        "playwright": playwright_ids(
            command(["npx", "playwright", "test", "--list"], ROOT / "web")
        ),
    }


def junit_keys(report: Path, runner: str) -> list[str]:
    """JUnit testcase별 안정적 ID로 수집 결과와 일대일 대응시킨다."""
    root = ET.parse(report).getroot()
    if root.tag not in ("testsuite", "testsuites"):
        raise ValueError(f"잘못된 JUnit 루트: {report.name}")
    cases = root.findall(".//testcase")
    if not cases:
        raise ValueError(f"JUnit 테스트 0건: {report.name}")
    ids = []
    for case in cases:
        classname = case.get("classname")
        name = case.get("name")
        if not classname or not name:
            raise ValueError(f"JUnit 필수 식별자 누락: {report.name}")
        if runner == "pytest":
            path = classname.replace(".", "/") + ".py"
        elif runner == "vitest":
            path = classname
        elif runner == "playwright":
            path = "web/e2e/" + Path(classname).name
        else:
            raise ValueError(f"미지원 Runner: {runner}")
        ids.append(stable_key(runner, f"{path}::{name}"))
    return ids


def reconcile(inventory: dict, reports: dict[str, list[Path]]) -> dict[str, int]:
    """누락·추가·중복 testcase가 있으면 총 개수만 같아도 실패 처리한다."""
    if inventory.get("schema_version") != 1 or not isinstance(inventory.get("tests"), list):
        raise ValueError("유효한 Inventory JSON이 아닙니다.")
    counts = {}
    for runner in RUNNERS:
        selected = [record for record in inventory["tests"] if record.get("runner") == runner]
        expected = [stable_key(runner, record["test_id"]) for record in selected]
        actual = [case for path in reports[runner] for case in junit_keys(path, runner)]
        expected_set, actual_set = set(expected), set(actual)
        if len(expected) != len(expected_set) or len(actual) != len(actual_set):
            raise ValueError(f"{runner} 수집 또는 JUnit에서 중복 Test ID")
        missing = expected_set - actual_set
        extra = actual_set - expected_set
        if missing or extra:
            # Test ID 전문에는 입력값 또는 개인 데이터가 포함될 수 있어 건수만 출력한다.
            raise ValueError(
                f"{runner} 인벤토리/실행 불일치: 수집={len(expected)}, "
                f"실행={len(actual)}, 누락={len(missing)}, 추가={len(extra)}"
            )
        counts[runner] = len(actual)
    return counts


def audit_removed(before: dict, after: dict) -> list[dict]:
    """이전 Inventory에서 사라진 Test ID와 보호 계약을 리뷰 근거로 추출한다."""
    previous = {
        (row["runner"], stable_key(row["runner"], row["test_id"])): row for row in before["tests"]
    }
    current = {(row["runner"], stable_key(row["runner"], row["test_id"])) for row in after["tests"]}
    return [
        {
            "runner": row["runner"],
            "source_path": row.get("source_path") or source_path(row["runner"], row["test_id"]),
            "contract_ids": row.get("contract_ids", []),
            "risk": row.get("risk", "review_required"),
        }
        for key, row in sorted(previous.items())
        if key not in current
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SOS 테스트 자산 인벤토리 관리")
    subs = parser.add_subparsers(dest="action")
    generate = subs.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    compare = subs.add_parser("reconcile")
    compare.add_argument("--inventory", type=Path, required=True)
    compare.add_argument("--pytest-report", action="append", type=Path, required=True)
    compare.add_argument("--vitest-report", action="append", type=Path, required=True)
    compare.add_argument("--playwright-report", action="append", type=Path, required=True)
    diff = subs.add_parser("diff")
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)
    # 이전 인터페이스 유지: '--output path'만 전달해도 generate 동작
    effective_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(
        ["generate", *effective_argv]
        if effective_argv and effective_argv[0].startswith("--")
        else effective_argv
    )
    try:
        if args.action == "generate":
            groups = collect()
            records = classify(groups, load_registry(args.registry))
            report = {"schema_version": 1, "tests": records}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                "Native 수집 성공: "
                + ", ".join(f"{name}={len(groups[name])}" for name in RUNNERS)
                + f", total={len(records)}, classified={sum(bool(x['contract_ids']) for x in records)}"
            )
            return 0
        if args.action == "reconcile":
            inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
            reports = {
                "pytest": args.pytest_report,
                "vitest": args.vitest_report,
                "playwright": args.playwright_report,
            }
            result = reconcile(inventory, reports)
            print("JUnit 실행·수집 ID 일치: " + ", ".join(f"{k}={v}" for k, v in result.items()))
            return 0
        if args.action == "diff":
            previous = json.loads(args.before.read_text(encoding="utf-8"))
            current = json.loads(args.after.read_text(encoding="utf-8"))
            removed = audit_removed(previous, current)
            print(json.dumps({"removed": removed}, ensure_ascii=False, indent=2))
            # 자동 승인/삭제 없음: Review 대상으로 다루기 전까지 Test ID 삭제는 실패
            return int(bool(removed))
        parser.print_help()
        return 2
    except (OSError, ValueError, KeyError, ET.ParseError, subprocess.CalledProcessError) as exc:
        print(
            f"테스트 수명주기 검증 실패: {exc.__class__.__name__}: {str(exc)[:160]}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
