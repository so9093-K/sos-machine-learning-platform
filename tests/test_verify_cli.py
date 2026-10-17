"""공통 검증 실행 계약의 실패 전달과 수집 범위를 검사한다."""

import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import importlib.util
import sys
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "scripts" / "verify.py"
spec = importlib.util.spec_from_file_location("sos_test_verify", source)
assert spec is not None and spec.loader is not None
verify = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


def test_fast_unit_suite_is_discovered_instead_of_hardcoded():
    paths = verify.unit_test_paths()
    assert paths
    assert "tests/test_ingestion.py" in paths
    assert "tests/test_verify_cli.py" in paths
    assert all(path.startswith("tests/test_") for path in paths)


def test_junit_missing_or_empty_is_not_success(tmp_path):
    path = tmp_path / "results.xml"
    assert not verify.verify_evidence(path, required=True)["valid"]
    path.write_text('<testsuite tests="0"></testsuite>')
    assert not verify.verify_evidence(path, required=True)["valid"]
    path.write_text("<invalid")
    assert not verify.verify_evidence(path, required=True)["valid"]


def test_failed_or_skipped_case_is_not_success(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text(
        '<testsuite><testcase name="a"><failure/></testcase>'
        '<testcase name="b"><skipped/></testcase></testsuite>'
    )
    evidence = verify.verify_evidence(path, required=True)
    assert evidence["valid"]
    assert evidence["reason"] == "테스트 실패 1건"
    path.write_text('<testsuite><testcase name="b"><skipped/></testcase></testsuite>')
    assert not verify.verify_evidence(path, required=True)["valid"]


def test_runner_exit_zero_with_missing_report_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "commands", lambda *_args: [(["true"], tmp_path)])
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="abc\n"),
    )
    code, manifest = verify.execute("python-unit", tmp_path)
    assert code == verify.EXIT_EVIDENCE
    assert manifest["status"] == "failed"
    assert manifest["evidence"]["reason"] == "JUnit 파일 누락"
    assert json.loads((tmp_path / "manifest.json").read_text())["exit_code"] == code


def test_successful_junit_and_nonzero_runner_fail(monkeypatch, tmp_path):
    report = tmp_path / "python-unit.xml"
    report.write_text('<testsuite><testcase name="registered"/></testsuite>')
    # 이전 실행 결과가 있어도 새 실행은 반드시 리포트를 삭제한 뒤 검사한다.
    monkeypatch.setattr(verify, "commands", lambda *_args: [(["false"], tmp_path)])
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="abc\n"),
    )
    code, manifest = verify.execute("python-unit", tmp_path)
    assert code != 0
    assert not report.exists()
    assert manifest["status"] == "failed"


def test_junit_suite_accepts_real_cases(tmp_path):
    path = tmp_path / "results.xml"
    root = ET.Element("testsuites")
    group = ET.SubElement(root, "testsuite")
    ET.SubElement(group, "testcase", name="alpha", classname="contract")
    ET.ElementTree(root).write(path)
    result = verify.verify_evidence(path, required=True)
    assert result == {"valid": True, "reason": None, "tests": 1}


def test_openapi_requires_base_ref(tmp_path):
    try:
        verify.commands("openapi-compat", tmp_path / "o.xml", None)
    except ValueError as exc:
        assert "--base-ref" in str(exc)
    else:
        raise AssertionError("PR 비교 기준이 누락됐는데도 성공")


def test_suite_contract_has_no_undeclared_runners():
    for suite in verify.SUITES.values():
        assert suite.kind in {"command", "junit"}
        if suite.name != "openapi-compat":
            assert verify.commands(suite.name, Path("/tmp/report.xml"), None)
