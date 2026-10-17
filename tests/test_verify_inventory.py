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
            "description": "등록 성공 상태",
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
    assert inventory.reconcile({"schema_version": 2, "tests": records}, cases) == {
        "pytest": 1,
        "vitest": 1,
        "playwright": 1,
    }
    records.pop()
    with pytest.raises(ValueError, match="불일치"):
        inventory.reconcile({"schema_version": 1, "tests": records}, cases)


def test_standard_removal_needs_no_extra_approval():
    before = {
        "schema_version": 2,
        "tests": [{"runner": "pytest", "test_id": "tests/test_a.py::standard", "contract_ids": []}],
    }
    after = {"schema_version": 2, "tests": []}
    result = inventory.audit_changes(before, after)
    assert result["standard_removed"] == ["tests/test_a.py"]
    assert result["protected_removed"] == []
    assert result["possible_renames"] == []


def test_protected_removal_blocks_even_with_other_test_in_same_contract():
    before = {
        "schema_version": 2,
        "tests": [
            {
                "runner": "pytest",
                "test_id": "tests/test_a.py::removed",
                "contract_ids": ["OWNER-001"],
            },
            {
                "runner": "pytest",
                "test_id": "tests/test_a.py::retained",
                "contract_ids": ["OWNER-001"],
            },
        ],
    }
    after = {"schema_version": 2, "tests": [before["tests"][1]]}
    assert inventory.audit_changes(before, after)["protected_removed"][0]["contract_ids"] == [
        "OWNER-001"
    ]


def test_rename_candidate_never_automatically_approved():
    before = {
        "schema_version": 2,
        "tests": [
            {"runner": "pytest", "test_id": "tests/test_a.py::old", "contract_ids": ["OWNER-001"]}
        ],
    }
    after = {
        "schema_version": 2,
        "tests": [
            {"runner": "pytest", "test_id": "tests/test_a.py::new", "contract_ids": ["OWNER-001"]}
        ],
    }
    result = inventory.audit_changes(before, after)
    assert len(result["possible_renames"]) == 1
    assert result["protected_removed"] == []


def test_protection_demotion_detected_without_test_removal():
    before = {
        "schema_version": 2,
        "tests": [
            {"runner": "pytest", "test_id": "tests/test_a.py::stays", "contract_ids": ["OWNER-001"]}
        ],
    }
    after = {
        "schema_version": 2,
        "tests": [{"runner": "pytest", "test_id": "tests/test_a.py::stays", "contract_ids": []}],
    }
    assert inventory.audit_changes(before, after)["lost_protections"]


def test_existing_v1_contract_is_still_protected():
    before = {
        "schema_version": 1,
        "tests": [
            {
                "runner": "pytest",
                "test_id": "tests/test_a.py::old",
                "contract_ids": ["OWNER-001"],
                "risk": "high",
            }
        ],
    }
    assert inventory.audit_changes(before, {"schema_version": 2, "tests": []})["protected_removed"]


def test_diff_cli_allows_standard_removal_but_blocks_protection(tmp_path, capsys):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    old = {
        "schema_version": 2,
        "tests": [{"runner": "pytest", "test_id": "tests/test_a.py::old", "contract_ids": []}],
    }
    before.write_text(__import__("json").dumps(old), encoding="utf-8")
    after.write_text('{"schema_version": 2, "tests": []}', encoding="utf-8")
    args = ["diff", "--before", str(before), "--after", str(after)]
    assert inventory.main(args) == 0
    assert "standard_removed" in capsys.readouterr().out
    old["tests"][0]["contract_ids"] = ["OWNER-001"]
    before.write_text(__import__("json").dumps(old), encoding="utf-8")
    assert inventory.main(args) == 1
    assert "protected_removed" in capsys.readouterr().out


def test_registry_rejects_missing_reason_and_old_schema(tmp_path):
    bad = {
        "schema_version": 2,
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
    bad["schema_version"] = 1
    with pytest.raises(ValueError):
        inventory.load_registry(target)


def test_empty_registry_defaults_to_standard(tmp_path):
    target = tmp_path / "contracts.json"
    target.write_text('{"schema_version": 2, "protected_contracts": []}', encoding="utf-8")
    assert inventory.load_registry(target) == []
    assert inventory.classify({"pytest": ["tests/test_a.py::case"]}, [])[0]["policy"] == "standard"


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
