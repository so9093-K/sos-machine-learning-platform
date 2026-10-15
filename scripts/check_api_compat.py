"""OpenAPI의 실제 사용 경로를 기준으로 입력·출력 방향별 변경 위험을 탐지한다.

프로젝트가 지원하는 OpenAPI subset의 보수적 검사이며 완전한 JSON Schema 판정기가 아니다.
"""

import argparse
import json
from pathlib import Path

METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def resolved(schema: dict, components: dict, seen: frozenset[str] = frozenset()) -> dict:
    while "$ref" in schema:
        pointer = schema["$ref"]
        if pointer in seen:
            return {"$ref": pointer}
        seen = seen | {pointer}
        if not pointer.startswith("#/components/schemas/"):
            return schema
        key = pointer.removeprefix("#/components/schemas/").replace("~1", "/").replace("~0", "~")
        schema = components.get(key, {})
    return schema


def schema_issues(
    old: dict, new: dict, kind: str, path: str, old_components: dict, new_components: dict,
    visited: frozenset[tuple[int, int]] = frozenset(),
) -> list[str]:
    """request는 입력 허용 범위 감소, response는 출력 보장 감소를 검출한다."""
    old = resolved(old, old_components)
    new = resolved(new, new_components)
    pair = (id(old), id(new))
    if pair in visited or old == new:
        return []
    visited = visited | {pair}
    union_key = next((k for k in ("anyOf", "oneOf") if k in old or k in new), None)
    if union_key:
        original = old.get(union_key, [old])
        updated = new.get(union_key, [new])
        # 입력: 이전에 허용한 대안을 계속 받아야 한다. 출력: 새 대안은 옛 클라이언트가 이해해야 한다.
        source, targets = (original, updated) if kind == "request" else (updated, original)
        for option in source:
            if not any(
                not schema_issues(
                    option if kind == "request" else target,
                    target if kind == "request" else option,
                    kind, path, old_components, new_components, visited,
                )
                for target in targets
            ):
                return [f"{kind} union narrowed/expanded: {path}"]
        return []
    if "allOf" in old or "allOf" in new:
        before, after = old.get("allOf", [old]), new.get("allOf", [new])
        if len(before) != len(after):
            return [f"{kind} composition changed: {path}"]
        return [
            issue
            for index, (a, b) in enumerate(zip(before, after))
            for issue in schema_issues(
                a, b, kind, f"{path}/allOf/{index}", old_components, new_components, visited
            )
        ]

    issues = []
    before_type, after_type = old.get("type"), new.get("type")
    if before_type != after_type and (before_type is not None or after_type is not None):
        issues.append(f"{kind} type changed: {path} ({before_type} -> {after_type})")
        return issues
    before_enum, after_enum = old.get("enum"), new.get("enum")
    if before_enum is not None or after_enum is not None:
        if before_enum is None or after_enum is None:
            # enum 제거는 입력 확장에는 안전하지만 출력 확장에서는 위험하다.
            if (kind == "request" and after_enum is not None) or (
                kind == "response" and before_enum is not None
            ):
                issues.append(f"{kind} enum changed: {path}")
        else:
            before = {json.dumps(x, sort_keys=True) for x in before_enum}
            after = {json.dumps(x, sort_keys=True) for x in after_enum}
            if (before - after if kind == "request" else after - before):
                issues.append(f"{kind} enum changed: {path}")

    before_fields = old.get("properties", {})
    after_fields = new.get("properties", {})
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    for field in before_fields.keys() - after_fields.keys():
        issues.append(f"{kind} field removed: {path}.{field}")
    newly_required = (
        new_required - old_required if kind == "request" else old_required - new_required
    )
    for field in sorted(newly_required):
        issues.append(f"{kind} required field changed: {path}.{field}")
    for field in before_fields.keys() & after_fields.keys():
        issues.extend(
            schema_issues(
                before_fields[field], after_fields[field], kind, f"{path}.{field}",
                old_components, new_components, visited
            )
        )
    if "items" in old and "items" in new:
        issues.extend(
            schema_issues(
                old["items"], new["items"], kind, f"{path}[]",
                old_components, new_components, visited
            )
        )
    if "additionalProperties" in old and "additionalProperties" in new:
        a, b = old["additionalProperties"], new["additionalProperties"]
        if isinstance(a, dict) and isinstance(b, dict):
            issues.extend(
                schema_issues(a, b, kind, f"{path}{{}}", old_components, new_components, visited)
            )
        elif a != b and (
            (kind == "request" and a is True and b is False)
            or (kind == "response" and a is False and b is True)
        ):
            issues.append(f"{kind} additional properties changed: {path}")
    return issues


def media_issues(old: dict, new: dict, kind: str, label: str, before: dict, after: dict):
    issues = []
    for media, value in old.get("content", {}).items():
        updated = new.get("content", {}).get(media)
        if updated is None:
            issues.append(f"{kind} media type removed: {label} {media}")
        else:
            issues.extend(
                schema_issues(
                    value.get("schema", {}), updated.get("schema", {}), kind,
                    f"{label} {media}", before, after,
                )
            )
    return issues


def breaking_changes(before: dict, after: dict) -> list[str]:
    issues = []
    old_components = before.get("components", {}).get("schemas", {})
    new_components = after.get("components", {}).get("schemas", {})
    for path, methods in before.get("paths", {}).items():
        updated = after.get("paths", {}).get(path, {})
        for method, old in methods.items():
            if method not in METHODS:
                continue
            new = updated.get(method)
            label = f"{method.upper()} {path}"
            if new is None:
                issues.append(f"endpoint removed: {label}")
                continue
            old_params = {(p.get("in"), p.get("name")): p for p in old.get("parameters", [])}
            new_params = {(p.get("in"), p.get("name")): p for p in new.get("parameters", [])}
            for key, param in new_params.items():
                old_param = old_params.get(key)
                if old_param is None:
                    if param.get("required"):
                        issues.append(f"new required parameter: {label} {key}")
                    continue
                if param.get("required") and not old_param.get("required"):
                    issues.append(f"parameter became required: {label} {key}")
                issues.extend(
                    schema_issues(
                        old_param.get("schema", {}), param.get("schema", {}), "request",
                        f"{label} parameter {key}", old_components, new_components,
                    )
                )
            for key in old_params.keys() - new_params.keys():
                issues.append(f"parameter removed: {label} {key}")
            old_body, new_body = old.get("requestBody", {}), new.get("requestBody", {})
            if not old_body.get("required") and new_body.get("required"):
                issues.append(f"request body became required: {label}")
            issues.extend(
                media_issues(
                    old_body, new_body, "request", f"{label} request body",
                    old_components, new_components,
                )
            )
            old_success = {s for s in old.get("responses", {}) if str(s).startswith("2")}
            new_success = {s for s in new.get("responses", {}) if str(s).startswith("2")}
            for code in old_success - new_success:
                issues.append(f"success status removed: {label} {code}")
            for code in old_success & new_success:
                issues.extend(
                    media_issues(
                        old["responses"][code], new["responses"][code], "response",
                        f"{label} {code}", old_components, new_components,
                    )
                )
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare base and current generated OpenAPI")
    parser.add_argument("base", type=Path)
    parser.add_argument("current", type=Path)
    args = parser.parse_args()
    issues = breaking_changes(
        json.loads(args.base.read_text(encoding="utf-8")),
        json.loads(args.current.read_text(encoding="utf-8")),
    )
    for issue in issues:
        print(f"BREAKING API: {issue}")
    if issues:
        return 1
    print("OpenAPI compatibility guard: no known breaking changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
