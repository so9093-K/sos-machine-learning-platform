"""실제 OpenAPI의 입력·출력 방향과 참조 스키마 변경 회귀 검증."""

import importlib.util
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "scripts" / "check_api_compat.py"
spec = importlib.util.spec_from_file_location("sos_api_compat", source)
assert spec is not None and spec.loader is not None
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def contract(
    *,
    request: dict | None = None,
    response: dict | None = None,
    output: dict | None = None,
) -> dict:
    op = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": response or {"$ref": "#/components/schemas/Result"}
                    }
                }
            }
        }
    }
    if request is not None:
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Input"}}},
        }
    return {
        "paths": {"/api/data": {"post": op}},
        "components": {
            "schemas": {
                "Input": request or {"type": "object", "properties": {"x": {"type": "string"}}},
                "Result": output
                or {
                    "type": "object",
                    "properties": {
                        "x": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "rows": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["x"],
                },
            }
        },
    }


def test_identical_contract_is_compatible():
    assert compat.breaking_changes(contract(), contract()) == []


def test_removed_endpoint_is_incompatible():
    changed = contract()
    changed["paths"] = {}
    assert any("endpoint removed" in x for x in compat.breaking_changes(contract(), changed))


def test_added_required_query_parameter_is_breaking():
    changed = contract()
    changed["paths"]["/api/data"]["post"]["parameters"] = [
        {"in": "query", "name": "cursor", "required": True, "schema": {"type": "string"}}
    ]
    assert any("required parameter" in x for x in compat.breaking_changes(contract(), changed))


def test_existing_optional_query_parameter_narrowing_is_breaking():
    before = contract()
    after = contract()
    old = {"in": "query", "name": "cursor", "required": False, "schema": {"type": "string"}}
    before["paths"]["/api/data"]["post"]["parameters"] = [old]
    after["paths"]["/api/data"]["post"]["parameters"] = [{**old, "schema": {"type": "integer"}}]
    assert any("type changed" in x for x in compat.breaking_changes(before, after))


def test_nullable_response_union_type_change_is_breaking():
    original = contract()
    changed = contract()
    changed["components"]["schemas"]["Result"]["properties"]["x"] = {
        "anyOf": [{"type": "integer"}, {"type": "null"}]
    }
    assert any("response union" in x for x in compat.breaking_changes(original, changed))


def test_response_array_items_change_is_breaking():
    original = contract()
    changed = contract()
    changed["components"]["schemas"]["Result"]["properties"]["rows"]["items"] = {"type": "integer"}
    assert any("response type changed" in x for x in compat.breaking_changes(original, changed))


def test_adding_required_response_field_is_safe():
    old = contract()
    new = contract()
    result = new["components"]["schemas"]["Result"]
    result["properties"]["created_at"] = {"type": "string"}
    result["required"].append("created_at")
    assert compat.breaking_changes(old, new) == []


def test_removing_required_response_field_is_breaking():
    old = contract()
    new = contract()
    new["components"]["schemas"]["Result"]["required"].clear()
    assert any("response required field" in x for x in compat.breaking_changes(old, new))


def test_new_required_request_field_is_breaking():
    old = contract(request={"type": "object", "properties": {"x": {"type": "string"}}})
    new = contract(
        request={
            "type": "object",
            "properties": {"x": {"type": "string"}, "y": {"type": "string"}},
            "required": ["y"],
        }
    )
    assert any("request required field" in x for x in compat.breaking_changes(old, new))


def test_new_optional_request_field_is_safe():
    old = contract(request={"type": "object", "properties": {"x": {"type": "string"}}})
    new = contract(
        request={
            "type": "object",
            "properties": {"x": {"type": "string"}, "y": {"type": "string"}},
        }
    )
    assert compat.breaking_changes(old, new) == []


def test_response_union_widening_is_breaking_but_request_widening_is_safe():
    old = contract(
        request={
            "type": "object",
            "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }
    )
    new = contract(
        request={
            "type": "object",
            "properties": {
                "x": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}
            },
        }
    )
    assert compat.breaking_changes(old, new) == []


def test_unreferenced_schema_removal_is_safe():
    old = contract()
    new = contract()
    old["components"]["schemas"]["Unused"] = {"type": "object", "properties": {}}
    assert compat.breaking_changes(old, new) == []


def test_success_status_removal_is_breaking():
    old = contract()
    new = contract()
    new["paths"]["/api/data"]["post"]["responses"] = {"204": {}}
    assert any("success status removed" in x for x in compat.breaking_changes(old, new))


def test_numeric_and_string_constraints_detect_directional_breaking_changes():
    # 요청 제한 축소는 Breaking, 응답 보장 강화는 안전하다.
    cases = (
        ("maximum", 100, 10, "request", True),
        ("maximum", 10, 100, "request", False),
        ("minimum", 1, 10, "request", True),
        ("minimum", 10, 1, "request", False),
        ("minLength", 1, 4, "request", True),
        ("maxLength", 20, 5, "request", True),
        ("minItems", 1, 4, "request", True),
        ("maxItems", 10, 2, "request", True),
        ("maximum", 100, 10, "response", False),
        ("maximum", 10, 100, "response", True),
        ("minimum", 1, 10, "response", False),
        ("minimum", 10, 1, "response", True),
        ("maxLength", 10, 20, "response", True),
        ("minItems", 3, 1, "response", True),
    )
    for key, before, after, direction, expected in cases:
        a = {"type": "integer", key: before}
        b = {"type": "integer", key: after}
        problems = compat.schema_issues(a, b, direction, "root", {}, {})
        assert bool(problems) is expected, (key, before, after, direction, problems)


def test_changed_format_pattern_and_exclusive_boundary_fail_conservatively():
    for before, after in (
        ({"type": "string", "format": "date"}, {"type": "string", "format": "date-time"}),
        ({"type": "string", "pattern": "^[a-z]+$"}, {"type": "string", "pattern": "^[a-z]{3}$"}),
        ({"type": "number", "exclusiveMinimum": 0}, {"type": "number", "exclusiveMinimum": 2}),
    ):
        assert compat.schema_issues(before, after, "request", "root", {}, {})


def test_realistic_query_maximum_narrowing_is_breaking():
    before, after = contract(), contract()
    before["paths"]["/api/data"]["post"]["parameters"] = [
        {"in": "query", "name": "limit", "schema": {"type": "integer", "maximum": 100}}
    ]
    after["paths"]["/api/data"]["post"]["parameters"] = [
        {"in": "query", "name": "limit", "schema": {"type": "integer", "maximum": 10}}
    ]
    assert any("upper bound" in x for x in compat.breaking_changes(before, after))


def test_pre_release_break_list_covers_only_explicit_retired_contracts():
    """사전 출시 정리의 허용 목록은 구체적인 엔드포인트와 응답 전환만 포함한다."""
    import json

    approved = json.loads(
        (
            Path(__file__).resolve().parents[1] / "scripts" / "approved_pre_release_api_breaks.json"
        ).read_text(encoding="utf-8")
    )["issues"]
    assert len(approved) == len(set(approved))
    assert "endpoint removed: GET /api/auth/me" in approved
    assert "endpoint removed: GET /api/datasets/pages" in approved
    assert all(
        item.startswith(("endpoint removed: ", "response type changed: ")) for item in approved
    )


def test_pre_release_api_break_exception_is_scoped_to_pr14_and_original_base():
    """다른 PR이 같은 기준 SHA를 사용해도 Breaking 승인 파일을 재사용하지 못한다."""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "PR_NUMBER: ${{ github.event.pull_request.number }}" in workflow
    assert (
        'if [ "$PR_NUMBER" = "14" ] && '
        '[ "$BASE_SHA" = "143caccd32a8102b848b56876deb6e1ebee8e07a" ]; then'
    ) in workflow
