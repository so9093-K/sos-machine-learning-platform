#!/usr/bin/env bash
set -euo pipefail

# OpenAPI는 FastAPI 앱에서 추출하며 중간 JSON을 소스 관리하지 않는다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="$(mktemp "${TMPDIR:-/tmp}/sos-openapi-XXXXXX.json")"
trap 'rm -f "$SCHEMA"' EXIT
cd "$ROOT"
uv run --locked python scripts/export_openapi.py > "$SCHEMA"
cd web
./node_modules/.bin/openapi-typescript "$SCHEMA" --output src/api.generated.ts
