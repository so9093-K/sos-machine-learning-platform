"""생성 OpenAPI의 이전 판본과의 호환성 회귀 검사."""

import importlib.util
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "scripts" / "check_api_compat.py"
spec = importlib.util.spec_from_file_location("sos_api_compat", source)
assert spec is not None and spec.loader is not None
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def contract(*, required: bool = False, extra: bool = False) -> dict:
    params = [{"in": "query", "name": "limit", "required": False}]
    if extra:
        params.append({"in": "query", "name": "cursor", "required": required})
    properties = {"id": {"type": "string"}, "name": {"type": "string"}}
    if extra:
        properties["optional"] = {"type": "string"}
    return {
        "paths": {"/api/datasets": {"get": {"parameters": params, "responses": {"200": {}}}}},
        "components": {
            "schemas": {
                "DatasetOut": {"type": "object", "properties": properties, "required": ["id"]}
            }
        },
    }


def test_compatible_addition():
    assert compat.breaking_changes(contract(), contract(extra=True)) == []


def test_removed_endpoint_is_incompatible():
    new = contract()
    new["paths"] = {}
    assert any("removed endpoint" in x for x in compat.breaking_changes(contract(), new))


def test_required_parameter_is_incompatible():
    assert any(
        "new required parameter" in x
        for x in compat.breaking_changes(contract(), contract(extra=True, required=True))
    )


def test_removed_response_field_is_incompatible():
    assert any(
        "removed field" in x for x in compat.breaking_changes(contract(extra=True), contract())
    )


def test_new_required_field_is_incompatible():
    new = contract()
    new["components"]["schemas"]["DatasetOut"]["required"].append("name")
    assert any("new required field" in x for x in compat.breaking_changes(contract(), new))


def test_removed_success_status_and_type_change_are_incompatible():
    new = contract()
    new["paths"]["/api/datasets"]["get"]["responses"] = {"204": {}}
    new["components"]["schemas"]["DatasetOut"]["properties"]["id"]["type"] = "integer"
    issues = compat.breaking_changes(contract(), new)
    assert any("removed success status" in x for x in issues)
    assert any("changed type: DatasetOut.id" in x for x in issues)
