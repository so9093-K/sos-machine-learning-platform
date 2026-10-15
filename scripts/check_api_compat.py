"""현재 OpenAPI를 PR base 계약과 비교하는 보수적인 하위 호환성 검사.

API 명세의 정본은 FastAPI 앱이다. 이 검사는 Git 각 판본에서 생성한 JSON만 비교한다.
완전한 JSON Schema 동치 판정은 아니며 공개 인터페이스의 명백한 호환성 파괴를 차단한다.
"""

import argparse
import json
from pathlib import Path

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def breaking_changes(before: dict, after: dict) -> list[str]:
    """제거된 경로·계약 필드 및 더 엄격해진 입력을 탐지한다."""
    problems = []
    old_paths, new_paths = before.get("paths", {}), after.get("paths", {})
    for path, old_verbs in old_paths.items():
        new_verbs = new_paths.get(path, {})
        for verb, old in old_verbs.items():
            if verb not in HTTP_METHODS:
                continue
            new = new_verbs.get(verb)
            if not new:
                problems.append(f"removed endpoint: {verb.upper()} {path}")
                continue
            old_params = {(x.get("in"), x.get("name")): x for x in old.get("parameters", [])}
            new_params = {(x.get("in"), x.get("name")): x for x in new.get("parameters", [])}
            for key, param in new_params.items():
                if key not in old_params and param.get("required"):
                    problems.append(f"new required parameter: {verb.upper()} {path} {key}")
                if (
                    key in old_params
                    and not old_params[key].get("required")
                    and param.get("required")
                ):
                    problems.append(f"parameter became required: {verb.upper()} {path} {key}")
            old_success = {s for s in old.get("responses", {}) if str(s).startswith("2")}
            new_success = {s for s in new.get("responses", {}) if str(s).startswith("2")}
            for status in old_success - new_success:
                problems.append(f"removed success status: {verb.upper()} {path} {status}")
            if not old.get("requestBody", {}).get("required") and new.get("requestBody", {}).get(
                "required"
            ):
                problems.append(f"request body became required: {verb.upper()} {path}")

    old_schemas = before.get("components", {}).get("schemas", {})
    new_schemas = after.get("components", {}).get("schemas", {})
    for name, old in old_schemas.items():
        new = new_schemas.get(name)
        if new is None:
            problems.append(f"removed component: {name}")
            continue
        for property_name in old.get("properties", {}):
            if property_name not in new.get("properties", {}):
                problems.append(f"removed field: {name}.{property_name}")
        old_required = set(old.get("required", []))
        new_required = set(new.get("required", []))
        for property_name in new_required - old_required:
            problems.append(f"new required field: {name}.{property_name}")
        if "type" in old and old["type"] != new.get("type"):
            problems.append(f"changed type: {name}")
        old_enum, new_enum = old.get("enum"), new.get("enum")
        if old_enum is not None and new_enum is not None:
            for value in old_enum:
                if value not in new_enum:
                    problems.append(f"removed enum value: {name} {value}")
        for property_name, old_prop in old.get("properties", {}).items():
            new_prop = new.get("properties", {}).get(property_name)
            if new_prop is None:
                continue
            for key in ("type", "format", "$ref"):
                if key in old_prop and old_prop[key] != new_prop.get(key):
                    problems.append(f"changed {key}: {name}.{property_name}")
    return sorted(problems)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare base and current generated OpenAPI")
    parser.add_argument("base", type=Path)
    parser.add_argument("current", type=Path)
    args = parser.parse_args()
    problems = breaking_changes(
        json.loads(args.base.read_text(encoding="utf-8")),
        json.loads(args.current.read_text(encoding="utf-8")),
    )
    for problem in problems:
        print(f"BREAKING API: {problem}")
    if problems:
        return 1
    print("OpenAPI compatibility guard: no known breaking changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
