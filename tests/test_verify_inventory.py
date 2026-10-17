"""Native Collector 출력으로 생성되는 테스트 ID와 누락 판정의 회귀 검사."""

import importlib.util
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "scripts" / "verify_inventory.py"
spec = importlib.util.spec_from_file_location("sos_test_verify_inventory", source)
assert spec is not None and spec.loader is not None
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def test_pytest_collection_does_not_include_summary():
    result = inventory.python_ids(
        "tests/test_a.py::test_one\ntests/integration/test_b.py::test_two\n2 tests collected\n"
    )
    assert result == [
        "tests/integration/test_b.py::test_two",
        "tests/test_a.py::test_one",
    ]


def test_vitest_native_tree_keeps_test_names():
    data = [
        {
            "file": "src/api.test.ts",
            "tasks": [{"type": "test", "name": "returns 201"}],
        }
    ]
    assert inventory.vitest_ids(data) == ["src/api.test.ts::returns 201"]


def test_playwright_list_collects_each_named_case():
    result = inventory.playwright_ids(
        "Listing tests:\n"
        "  [chromium] › workspace.spec.ts:12:1 › registration 201\n"
        "  [chromium] › auth.spec.ts:4:1 › viewer cannot upload\n"
        "Total: 2 tests in 2 files\n"
    )
    assert result == [
        "auth.spec.ts:4:1::viewer cannot upload",
        "workspace.spec.ts:12:1::registration 201",
    ]

def test_inventory_vitest_uses_same_src_scope_as_frontend(monkeypatch):
    calls = []

    def fake_command(args, _cwd):
        calls.append(args)
        if args[0] == "uv":
            return "tests/test_a.py::test_case\n"
        if args[1] == "vitest":
            return '[{"file":"src/unit.test.ts","tasks":[{"name":"case"}]}]'
        return "  [chromium] › workspace.spec.ts:1:1 › registration\n"

    monkeypatch.setattr(inventory, "command", fake_command)
    records = inventory.collect()
    assert ["npx", "vitest", "list", "src", "--json"] in calls
    assert {record["runner"] for record in records} == {"pytest", "vitest", "playwright"}
