#!/usr/bin/env bash
set -euo pipefail

# 호스트 개발과 Docker 빌드에서 동일한 고정 자산을 준비한다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/tools/scalar-docs"
npm ci --omit=dev --ignore-scripts --no-audit --no-fund
sha256sum --check standalone.sha256
mkdir -p "$ROOT/src/sos_platform/static"
cp node_modules/@scalar/api-reference/dist/browser/standalone.js \
  "$ROOT/src/sos_platform/static/scalar.js"
