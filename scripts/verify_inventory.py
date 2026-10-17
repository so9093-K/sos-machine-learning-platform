"""테스트 자산 인벤토리·보호 계약·실행 증거의 Native Runner 대조.

테스트 정의의 정본은 pytest·Vitest·Playwright이며 Registry는 보호 의미만 저장한다.
"""

import argparse
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
SCOPE_FILE = ROOT / "scripts" / "inventory_scope.json"
# Runner별 수집 명령·경로만 프로젝트 설정에서 읽으며 테스트 의미와 대조 로직은 공유한다.
SCOPES = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
RUNNERS = ("pytest", "vitest", "playwright")
if set(SCOPES) != set(RUNNERS):
    raise ValueError("Native Runner 설정이 누락되었거나 중복되었습니다.")


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
            if line.strip().startswith(SCOPES["pytest"]["source_root"] + "/") and "::" in line
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
        result.add(f"{SCOPES['playwright']['source_root']}/{filename}::{'::'.join(names)}")
    return sorted(result)


def source_path(runner: str, test_id: str) -> str:
    """Native Runner별 경로 표현을 저장소 상대 경로로 정규화한다."""
    source = test_id.split("::", 1)[0].replace("\\", "/")
    if runner == "pytest":
        if not source.startswith(SCOPES["pytest"]["source_root"] + "/"):
            raise ValueError(f"pytest 출처가 설정한 source_root 밖입니다: {source}")
        return source
    if runner == "vitest":
        root = SCOPES["vitest"]["source_root"]
        cwd = SCOPES["vitest"]["cwd"]
        if source.startswith(root + "/"):
            return source
        if f"/{root}/" in source:
            return root + "/" + source.rsplit(f"/{root}/", 1)[1]
        relative = root.removeprefix(cwd + "/")
        if source.startswith(relative + "/"):
            return cwd + "/" + source
        raise ValueError("Vitest 출처가 정의된 source_root 내부가 아닙니다.")
    if runner == "playwright":
        if source.startswith(SCOPES["playwright"]["source_root"] + "/"):
            return source
        if source.endswith(".spec.ts"):
            return SCOPES["playwright"]["source_root"] + "/" + Path(source).name
        raise ValueError("Playwright 출처가 설정한 source_root 밖입니다.")
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
    """위험도 대신 명시적 Protected 계약과 근거만 관리한다."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "protected_contracts"}:
        raise ValueError("보호 계약 Registry 필드 오류")
    if value["schema_version"] != 1:
        raise ValueError("보호 계약 Registry 버전 오류")
    contracts = value["protected_contracts"]
    if not isinstance(contracts, list):
        raise ValueError("보호 계약 목록 형식 오류")
    seen = set()
    for contract in contracts:
        if not isinstance(contract, dict) or set(contract) != {"id", "reason", "selectors"}:
            raise ValueError("보호 계약 필드 오류")
        identifier = contract["id"]
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Z0-9]+(?:-[A-Z0-9]+)+", identifier
        ):
            raise ValueError("Contract ID 형식 오류")
        if identifier in seen:
            raise ValueError(f"중복 Contract ID: {identifier}")
        seen.add(identifier)
        if not isinstance(contract["reason"], str) or not contract["reason"].strip():
            raise ValueError(f"보호 계약 선정 이유 누락: {identifier}")
        selectors = contract["selectors"]
        if not isinstance(selectors, list) or not selectors:
            raise ValueError(f"보호 계약 연결 선택자 누락: {identifier}")
        selected = set()
        for selector in selectors:
            if not isinstance(selector, dict) or set(selector) != {
                "runner",
                "path",
                "title",
            }:
                raise ValueError(f"보호 계약 선택자 필드 오류: {identifier}")
            runner = selector["runner"]
            if runner not in RUNNERS:
                raise ValueError(f"보호 계약 Runner 오류: {identifier}")
            pattern = selector["path"]
            if (
                not isinstance(pattern, str)
                or not pattern.startswith(SCOPES[runner]["source_root"] + "/")
                or any(char in pattern for char in "*?[]")
            ):
                raise ValueError(f"보호 계약 경로 오류: {identifier}")
            title = selector["title"]
            if not isinstance(title, str) or not title.strip() or any(x in title for x in "*?[]"):
                raise ValueError(f"보호 계약 제목 조건 오류: {identifier}")
            if (runner, pattern, title) in selected:
                raise ValueError(f"보호 계약 중복 선택자: {identifier}")
            selected.add((runner, pattern, title))
    return contracts


def classify(groups: dict[str, list[str]], registry: list[dict]) -> list[dict]:
    """계약 연결이 있는 테스트만 Protected로, 나머지는 자동 Standard로 둔다."""
    records = []
    matched = Counter()
    for runner, items in groups.items():
        if runner not in RUNNERS or not items:
            raise ValueError(f"Native Runner 수집 누락 또는 미지원: {runner}")
        for identifier in items:
            path = source_path(runner, identifier)
            name = test_title(identifier)
            links = []
            for contract in registry:
                for index, selector in enumerate(contract["selectors"]):
                    if runner != selector["runner"]:
                        continue
                    title = selector["title"]
                    if runner == "pytest":
                        title_matches = name == title or name.startswith(title + "[")
                    elif runner == "playwright":
                        title_matches = name == title or name.endswith("::" + title)
                    else:
                        title_matches = name == title or name.endswith(" > " + title)
                    if path == selector["path"] and title_matches:
                        links.append(contract["id"])
                        matched[(contract["id"], index)] += 1
                        break
            records.append(
                {
                    "runner": runner,
                    "test_id": identifier,
                    "source_path": path,
                    "layer": (
                        "integration"
                        if runner == "pytest"
                        and path.startswith(SCOPES["pytest"]["integration_root"] + "/")
                        else SCOPES[runner]["layer"]
                    ),
                    "contract_ids": sorted(links),
                    "policy": "protected" if links else "standard",
                }
            )
    for contract in registry:
        for index, _ in enumerate(contract["selectors"]):
            if matched[(contract["id"], index)] == 0:
                raise ValueError(f"보호 계약 선택자 미수집: {contract['id']} #{index + 1}")
    return records


def collect() -> dict[str, list[str]]:
    """실제 Native Runner 목록을 프로젝트별 수집 설정으로 얻는다."""
    parsers = {
        "pytest": python_ids,
        "vitest": lambda text: vitest_ids(json.loads(text)),
        "playwright": playwright_ids,
    }
    return {
        runner: parsers[runner](command(SCOPES[runner]["collect"], ROOT / SCOPES[runner]["cwd"]))
        for runner in RUNNERS
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
            path = SCOPES["playwright"]["source_root"] + "/" + Path(classname).name
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SOS 테스트 자산 인벤토리 관리")
    subs = parser.add_subparsers(dest="action", required=True)
    generate = subs.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    compare = subs.add_parser("reconcile")
    compare.add_argument("--inventory", type=Path, required=True)
    compare.add_argument("--pytest-report", action="append", type=Path, required=True)
    compare.add_argument("--vitest-report", action="append", type=Path, required=True)
    compare.add_argument("--playwright-report", action="append", type=Path, required=True)
    args = parser.parse_args(argv)
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
                + f", total={len(records)}, protected={sum(bool(x['contract_ids']) for x in records)}"
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
    except (OSError, ValueError, KeyError, ET.ParseError, subprocess.CalledProcessError) as exc:
        print(
            f"테스트 수명주기 검증 실패: {exc.__class__.__name__}: {str(exc)[:160]}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
