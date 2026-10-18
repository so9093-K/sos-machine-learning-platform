"""Native Runner를 사용하는 SOS의 제공자 독립 검증 실행 계약.

CI YAML은 Suite를 선택하고 환경을 준비하지만 검증의 의미를 재정의하지 않는다.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_EVIDENCE = 3


@dataclass(frozen=True)
class Suite:
    """검증 단위의 실행 경로와 보고 계약."""

    name: str
    kind: str
    infrastructure: bool
    description: str


SUITES = {
    suite.name: suite
    for suite in (
        Suite("ruff", "command", False, "Python lint와 format"),
        Suite("python-unit", "junit", False, "인프라 없는 Python 테스트"),
        Suite("python-integration", "junit", True, "DB·S3·Iceberg 통합 테스트"),
        Suite("frontend", "junit", False, "Vitest 및 TypeScript/Vite"),
        Suite("openapi-types", "command", False, "생성된 OpenAPI 타입 정합성"),
        Suite("openapi-compat", "command", False, "PR 기준 API 후방 호환성"),
        Suite("wheel", "command", False, "설치된 Wheel에 포함된 Alembic 검증"),
        Suite("alembic", "command", True, "Alembic 스키마 정합성"),
        Suite("workspace-e2e", "junit", True, "격리된 브라우저 사용자 흐름"),
        Suite("token-rbac", "junit", True, "토큰 역할별 브라우저 흐름"),
        Suite("make-lifecycle", "command", True, "Make 재시작·데이터 보존"),
        Suite("container-runtime", "command", True, "전체 Compose 컨테이너·권한·데이터 보존"),
    )
}


def unit_test_paths() -> list[str]:
    """테스트 파일 정본을 수작업으로 복제하지 않고 위치로 선택한다."""
    return [str(path.relative_to(ROOT)) for path in sorted((ROOT / "tests").glob("test_*.py"))]


def commands(name: str, report: Path, base_ref: str | None) -> list[tuple[list[str], Path]]:
    """Suite별 Native Runner 호출을 동일한 저장소 계약으로 정의한다."""
    uv = ["uv", "run", "--locked"]
    if name == "ruff":
        return [
            (uv + ["ruff", "check", "."], ROOT),
            (uv + ["ruff", "format", "--check", "."], ROOT),
        ]
    if name == "python-unit":
        return [(uv + ["pytest", "-q", *unit_test_paths(), f"--junitxml={report}"], ROOT)]
    if name == "python-integration":
        return [(uv + ["pytest", "-q", "tests/integration", f"--junitxml={report}"], ROOT)]
    if name == "frontend":
        return [
            (["npm", "run", "build"], ROOT / "web"),
            (
                [
                    "npm",
                    "run",
                    "test",
                    "--",
                    "--reporter=default",
                    "--reporter=junit",
                    f"--outputFile.junit={report}",
                ],
                ROOT / "web",
            ),
        ]
    if name == "openapi-types":
        return [
            (["npm", "run", "generate:types"], ROOT / "web"),
            (["git", "diff", "--exit-code", "--", "web/src/api.generated.ts"], ROOT),
        ]
    if name == "openapi-compat":
        if not base_ref:
            raise ValueError("openapi-compat는 --base-ref가 필요합니다.")
        return [
            (
                uv
                + [
                    "python",
                    "scripts/verify_openapi.py",
                    "--base-ref",
                    base_ref,
                ],
                ROOT,
            )
        ]
    if name == "wheel":
        return [(uv + ["python", "scripts/verify_wheel.py"], ROOT)]
    if name == "alembic":
        return [(uv + ["alembic", "check"], ROOT)]
    if name == "workspace-e2e":
        return [(uv + ["python", "scripts/run_isolated_e2e.py"], ROOT)]
    if name == "token-rbac":
        return [(uv + ["python", "scripts/verify_token_rbac.py"], ROOT)]
    if name == "make-lifecycle":
        return [(uv + ["python", "scripts/verify_make_lifecycle.py"], ROOT)]
    if name == "container-runtime":
        return [(["python3", "scripts/verify_container_runtime.py"], ROOT)]
    raise ValueError(f"알 수 없는 Suite: {name}")


def junit_cases(report: Path) -> tuple[int, int, int, list[str]]:
    """JUnit XML의 실제 testcase로 성공/실패/미실행을 판정한다."""
    root = ET.parse(report).getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit 루트가 testsuite(s)가 아닙니다.")
    cases = root.findall(".//testcase")
    if root.tag == "testsuite":
        cases = root.findall("./testcase")
    if not cases:
        raise ValueError("JUnit testcase 0건")
    ids = []
    failed = skipped = 0
    for case in cases:
        name = case.get("name")
        if not name:
            raise ValueError("testcase의 name 누락")
        ids.append(f"{case.get('classname', '')}::{name}")
        failed += int(case.find("failure") is not None or case.find("error") is not None)
        skipped += int(case.find("skipped") is not None)
    return len(cases), failed, skipped, ids


def verify_evidence(report: Path, *, required: bool) -> dict:
    """정상 종료 여부와 별개로 테스트 0건·손상·누락을 성공 처리하지 않는다."""
    if not required:
        return {"valid": True, "reason": None, "tests": None}
    if not report.is_file():
        return {"valid": False, "reason": "JUnit 파일 누락", "tests": 0}
    try:
        total, failed, skipped, _ = junit_cases(report)
    except (OSError, ET.ParseError, ValueError) as exc:
        return {"valid": False, "reason": f"JUnit 검증 실패: {exc}", "tests": 0}
    if failed:
        return {"valid": True, "reason": f"테스트 실패 {failed}건", "tests": total}
    if skipped:
        return {"valid": False, "reason": f"필수 Suite 테스트 Skip {skipped}건", "tests": total}
    return {"valid": True, "reason": None, "tests": total}


def default_output() -> Path:
    """기존 JUnit의 stale 결과와 충돌하지 않는 고유 실행 경로."""
    base = ROOT / ".sos-dev" / "validation"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=base))


def execute(
    name: str, output: Path, *, base_ref: str | None = None, env: dict | None = None
) -> tuple[int, dict]:
    """실패를 보존하고 실행 증거가 없으면 성공 종료하지 않는다."""
    if name not in SUITES:
        raise ValueError(f"알 수 없는 Suite: {name}")
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    report = output / f"{name}.xml"
    report.unlink(missing_ok=True)
    suite = SUITES[name]
    started = time.monotonic()
    result = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "suite": name,
        "git_sha": None,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "provider": "github" if os.getenv("GITHUB_ACTIONS") == "true" else "local",
        "platform": sys.platform,
        "status": "failed",
        "exit_code": None,
        "elapsed_seconds": None,
        "report": report.name if suite.kind == "junit" else None,
        "evidence": None,
    }
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if revision.returncode == 0:
        result["git_sha"] = revision.stdout.strip()
    status = 0
    error = None
    run_env = dict(os.environ if env is None else env)
    if name in {"workspace-e2e", "token-rbac"}:
        run_env["SOS_JUNIT_FILE"] = str(report)
    try:
        planned = commands(name, report, base_ref)
        if not planned:
            raise ValueError("Suite의 필수 실행 명령이 비어 있습니다.")
        for command, cwd in planned:
            print(f"[SOS Verify] {name}: {' '.join(command)}", flush=True)
            proc = subprocess.run(command, cwd=cwd, env=run_env, check=False)
            if proc.returncode:
                status = EXIT_FAILED
                error = f"명령 실패 (exit={proc.returncode}): {command[0]}"
                break
    except (OSError, ValueError) as exc:
        status = EXIT_USAGE if isinstance(exc, ValueError) else EXIT_FAILED
        error = str(exc)
    evidence = verify_evidence(report, required=suite.kind == "junit")
    if not evidence["valid"]:
        status = EXIT_EVIDENCE if status == 0 else status
        error = evidence["reason"] if error is None else f"{error}; {evidence['reason']}"
    if evidence["reason"] and not error:
        status = EXIT_FAILED
        error = evidence["reason"]
    result.update(
        status="passed" if status == 0 else "failed",
        exit_code=status,
        elapsed_seconds=round(time.monotonic() - started, 3),
        evidence=evidence,
    )
    if error:
        result["error"] = error
    try:
        (output / f"{name}.manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / f"{name}.summary.txt").write_text(
            f"{name}: {result['status']} (exit={status}, tests={evidence['tests']}, "
            f"elapsed={result['elapsed_seconds']}s)\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[SOS Verify] Manifest/Summary 저장 실패: {exc}", file=sys.stderr)
        return EXIT_EVIDENCE, result
    return status, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SOS 공통 검증 실행 계약")
    commands_parser = parser.add_subparsers(dest="action", required=True)
    commands_parser.add_parser("list")
    inventory = commands_parser.add_parser("inventory")
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument("--registry", type=Path)
    reconcile = commands_parser.add_parser("reconcile")
    reconcile.add_argument("--inventory", type=Path, required=True)
    for runner in ("pytest", "vitest", "playwright"):
        reconcile.add_argument(f"--{runner}-report", type=Path, action="append", required=True)
    run = commands_parser.add_parser("run")
    run.add_argument("suite", choices=sorted(SUITES))
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--base-ref")
    args = parser.parse_args(argv)
    if args.action == "list":
        for suite in SUITES.values():
            print(
                f"{suite.name:<20} {'infra' if suite.infrastructure else 'fast':<6} {suite.description}"
            )
        return 0
    if args.action == "inventory":
        command = [
            "uv",
            "run",
            "--locked",
            "python",
            "scripts/verify_inventory.py",
            "generate",
            "--output",
            str(args.output),
        ]
        if args.registry is not None:
            command += ["--registry", str(args.registry)]
        return subprocess.run(command, cwd=ROOT, check=False).returncode
    if args.action == "reconcile":
        command = [
            "python3",
            "scripts/verify_inventory.py",
            "reconcile",
            "--inventory",
            str(args.inventory),
        ]
        for runner in ("pytest", "vitest", "playwright"):
            for report in getattr(args, f"{runner}_report"):
                command += [f"--{runner}-report", str(report)]
        return subprocess.run(command, cwd=ROOT, check=False).returncode
    output = args.output_dir or default_output()
    status, outcome = execute(args.suite, output, base_ref=args.base_ref)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
