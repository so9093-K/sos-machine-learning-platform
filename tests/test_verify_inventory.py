"""Native 수집·보호 계약·JUnit 증거의 실패 경로를 검사한다."""

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "scripts" / "verify_inventory.py"
spec = importlib.util.spec_from_file_location("sos_test_verify_inventory", source)
assert spec is not None and spec.loader is not None
inventory = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inventory
spec.loader.exec_module(inventory)


def sample_registry():
    """실행 환경 없이 계약 대조에 사용할 최소 메타데이터."""
    return [
        {
            "id": "DATASET-REGISTER-201",
            "reason": "확정된 등록 성공 상태를 보호한다.",
            "selectors": [
                {
                    "runner": "playwright",
                    "path_glob": "web/e2e/workspace.spec.ts",
                    "title_glob": "*registration remains successful*",
                }
            ],
        }
    ]


def test_pytest_collect_preserves_real_node_ids():
    assert inventory.python_ids(
        "tests/test_a.py::test_one\n"
        "tests/integration/test_b.py::test_two[param]\n2 tests collected\n"
    ) == [
        "tests/integration/test_b.py::test_two[param]",
        "tests/test_a.py::test_one",
    ]


def test_vitest_collect_preserves_files_and_titles():
    data = [{"file": "src/api.test.ts", "tasks": [{"name": "suite > returns 201"}]}]
    assert inventory.vitest_ids(data) == ["src/api.test.ts::suite > returns 201"]


def test_playwright_collects_top_level_and_nested_test_lines():
    output = (
        "Listing tests:\n"
        "  workspace.spec.ts:283:1 › 201 registration remains successful\n"
        "  [chromium] › workspace.spec.ts:323:1 › dismissed 201 success notice\n"
        "  auth.spec.ts:9:3 › token identity and resource permissions › viewer can read\n"
        "Total: 3 tests in 2 files\n"
    )
    assert inventory.playwright_ids(output) == [
        "web/e2e/auth.spec.ts::token identity and resource permissions::viewer can read",
        "web/e2e/workspace.spec.ts::201 registration remains successful",
        "web/e2e/workspace.spec.ts::dismissed 201 success notice",
    ]


def test_registry_maps_protection_with_standard_default():
    groups = {
        "pytest": ["tests/test_a.py::test_one"],
        "vitest": ["src/api.test.ts::suite > success"],
        "playwright": [
            "web/e2e/workspace.spec.ts::201 registration remains successful",
            "web/e2e/workspace.spec.ts::unrelated navigation",
        ],
    }
    result = inventory.classify(groups, sample_registry())
    assert len(result) == 4
    protected = next(x for x in result if "201 registration" in x["test_id"])
    assert protected["policy"] == "protected"
    assert protected["contract_ids"] == ["DATASET-REGISTER-201"]
    unprotected = next(x for x in result if "unrelated navigation" in x["test_id"])
    assert unprotected["policy"] == "standard"
    assert unprotected["contract_ids"] == []
    assert all("risk" not in row and "lifecycle" not in row for row in result)


def test_contract_selector_not_collected_is_error():
    with pytest.raises(ValueError, match="선택자 미수집"):
        inventory.classify(
            {
                "pytest": ["tests/test_a.py::test_one"],
                "vitest": ["src/a.test.ts::a"],
                "playwright": ["web/e2e/workspace.spec.ts::other"],
            },
            sample_registry(),
        )


def test_protected_selector_requires_precise_test_name(tmp_path):
    """파일 전체나 전역 와일드카드로 새 Standard 테스트를 보호 분류하지 않는다."""
    import json

    for title in (None, "*"):
        selector = {"runner": "pytest", "path_glob": "tests/test_a.py"}
        if title is not None:
            selector["title_glob"] = title
        data = {
            "schema_version": 1,
            "protected_contracts": [{
                "id": "SCOPE-001",
                "reason": "보호 대상 식별",
                "selectors": [selector],
            }],
        }
        path = tmp_path / "contracts.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="선택자 필드|제목 조건"):
            inventory.load_registry(path)


def test_protected_selector_rejects_file_glob(tmp_path):
    """파일 이름 자체를 광범위 패턴으로 지정하지 않는다."""
    import json

    data = {
        "schema_version": 1,
        "protected_contracts": [{
            "id": "SCOPE-001",
            "reason": "보호 대상 식별",
            "selectors": [{
                "runner": "pytest",
                "path_glob": "tests/integration/test_*.py",
                "title_glob": "test_stale_attempt_cannot_complete_or_publish_new_attempt",
            }],
        }],
    }
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="경로"):
        inventory.load_registry(path)


def test_specific_protection_leaves_other_tests_in_same_file_standard():
    """같은 파일의 페이지네이션 테스트는 Attempt 게시 계약 보호가 아니다."""
    contracts = [{
        "id": "STATE-001",
        "reason": "구형 Attempt 결과 게시 차단",
        "selectors": [{
            "runner": "pytest",
            "path_glob": "tests/integration/test_jobs.py",
            "title_glob": "test_stale_attempt_cannot_complete_or_publish_new_attempt*",
        }],
    }]
    ids = {"pytest": [
        "tests/integration/test_jobs.py::test_stale_attempt_cannot_complete_or_publish_new_attempt",
        "tests/integration/test_jobs.py::test_job_cursor_pagination_has_no_duplicates_or_missing_items",
    ]}
    records = inventory.classify(ids, contracts)
    assert [record["policy"] for record in records] == ["protected", "standard"]


def test_protected_test_missing_fails_even_if_file_has_other_tests():
    """보호할 실패 경로가 사라지면 해당 파일 내 다른 테스트가 있어도 실패한다."""
    contracts = [{
        "id": "STATE-001",
        "reason": "구형 Attempt 결과 게시 차단",
        "selectors": [{
            "runner": "pytest",
            "path_glob": "tests/integration/test_jobs.py",
            "title_glob": "test_stale_attempt_cannot_complete_or_publish_new_attempt*",
        }],
    }]
    ids = {"pytest": [
        "tests/integration/test_jobs.py::test_job_cursor_pagination_has_no_duplicates_or_missing_items"
    ]}
    with pytest.raises(ValueError, match="선택자 미수집"):
        inventory.classify(ids, contracts)


def test_registry_real_policy_has_unique_ids():
    contracts = inventory.load_registry(inventory.DEFAULT_REGISTRY)
    assert len(contracts) >= 6
    assert any(x["id"] == "ICEBERG-WRITE-OWNER-001" for x in contracts)
    assert all(x["reason"].strip() for x in contracts)


def test_vitest_absolute_path_normalizes():
    test_id = "/home/runner/work/my-repo/web/src/api.test.ts::Suite > case"
    assert inventory.stable_key("vitest", test_id) == "web/src/api.test.ts::Suite::case"


def test_junit_reconcile_recovers_all_playwright_cases(tmp_path):
    datasets = [
        ("pytest", "tests/test_a.py::test_one", "tests.test_a", "test_one"),
        ("vitest", "src/api.test.ts::Suite > ok", "src/api.test.ts", "Suite > ok"),
        (
            "playwright",
            "web/e2e/workspace.spec.ts::registration remains successful",
            "workspace.spec.ts",
            "registration remains successful",
        ),
    ]
    cases = {}
    records = []
    for runner, identifier, classname, title in datasets:
        report = tmp_path / f"{runner}.xml"
        root = ET.Element("testsuite")
        ET.SubElement(root, "testcase", classname=classname, name=title)
        ET.ElementTree(root).write(report, encoding="unicode")
        cases[runner] = [report]
        records.append({"runner": runner, "test_id": identifier})
    assert inventory.reconcile({"schema_version": 1, "tests": records}, cases) == {
        "pytest": 1,
        "vitest": 1,
        "playwright": 1,
    }
    records.pop()
    with pytest.raises(ValueError, match="불일치"):
        inventory.reconcile({"schema_version": 1, "tests": records}, cases)


def test_registry_rejects_missing_reason_and_invalid_schema(tmp_path):
    bad = {
        "schema_version": 1,
        "protected_contracts": [
            {
                "id": "OWNER-001",
                "reason": "",
                "selectors": [{"runner": "pytest", "path_glob": "tests/test_a.py"}],
            }
        ],
    }
    target = tmp_path / "contracts.json"
    target.write_text(__import__("json").dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="선정 이유"):
        inventory.load_registry(target)
    bad["protected_contracts"][0]["reason"] = "보호 계약의 안정적인 선정 이유"
    bad["schema_version"] = 999
    target.write_text(__import__("json").dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="버전"):
        inventory.load_registry(target)


def test_empty_registry_defaults_to_standard(tmp_path):
    target = tmp_path / "contracts.json"
    target.write_text('{"schema_version": 1, "protected_contracts": []}', encoding="utf-8")
    assert inventory.load_registry(target) == []
    assert inventory.classify({"pytest": ["tests/test_a.py::case"]}, [])[0]["policy"] == "standard"


def test_python_collector_scope_is_project_configured(monkeypatch):
    """pytest 테스트 루트가 달라도 Native Node ID를 유지한다."""
    monkeypatch.setitem(inventory.SCOPES["pytest"], "source_root", "specs")
    assert inventory.python_ids("specs/test_a.py::works\n") == ["specs/test_a.py::works"]
    assert inventory.source_path("pytest", "specs/test_a.py::works") == "specs/test_a.py"


def test_vitest_source_scope_is_project_configured(monkeypatch):
    """Vitest 경로를 변경해도 JUnit/Native 정규화가 일치한다."""
    monkeypatch.setitem(inventory.SCOPES["vitest"], "cwd", "frontend")
    monkeypatch.setitem(inventory.SCOPES["vitest"], "source_root", "frontend/specs")
    assert inventory.source_path("vitest", "specs/api.test.ts::case") == (
        "frontend/specs/api.test.ts"
    )
    assert inventory.source_path("vitest", "/workspace/frontend/specs/api.test.ts::case") == (
        "frontend/specs/api.test.ts"
    )


def test_project_runner_scope_changes_do_not_require_core_modification(monkeypatch):
    """Runner별 프로젝트 경로를 설정만으로 변경할 수 있다."""
    monkeypatch.setitem(inventory.SCOPES["playwright"], "source_root", "browser/specs")
    listed = inventory.playwright_ids("  workspace.spec.ts:10:2 › sample\n")
    assert listed == ["browser/specs/workspace.spec.ts::sample"]
    assert inventory.source_path("playwright", "workspace.spec.ts::sample") == (
        "browser/specs/workspace.spec.ts"
    )


def test_inventory_runner_scopes_are_independent(monkeypatch):
    calls = []

    def fake_command(args, _cwd):
        calls.append(args)
        if args[0] == "uv":
            return "tests/test_a.py::test_case\n"
        if args[1] == "vitest":
            return '[{"file":"src/unit.test.ts","tasks":[{"name":"case"}]}]'
        return "  workspace.spec.ts:1:1 › registration\n"

    monkeypatch.setattr(inventory, "command", fake_command)
    groups = inventory.collect()
    assert len(groups["playwright"]) == 1
    assert ["npx", "vitest", "list", "src", "--json"] in calls
