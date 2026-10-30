# Scalar는 빌드 시에만 Node를 사용한다. Python 실행 이미지에는 번들만 복사한다.
FROM node:24-bookworm-slim AS scalar-assets
WORKDIR /build
COPY tools/scalar-docs/ ./tools/scalar-docs/
COPY scripts/build_scalar_assets.sh ./scripts/
RUN bash scripts/build_scalar_assets.sh

# API·초기화와 Worker는 같은 잠금 파일에서 필요한 의존성 그룹만 선택한다.
FROM ghcr.io/astral-sh/uv:0.9.2 AS uv
# Model Revision은 학습 Runtime을 기록하므로 다시 빌드해도 Python이 바뀌지 않도록 digest로 고정한다.
FROM python:3.12.16-slim-bookworm@sha256:dbecf082dea1850bcd45039ab8658daf263620e5f662944a15dd508dc851f7a3

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SOS_HOME=/var/lib/sos \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock .python-version ./
COPY src/ ./src/
COPY --from=scalar-assets /build/src/sos_platform/static/scalar.js ./src/sos_platform/static/scalar.js
COPY migrations/ ./migrations/
COPY alembic.ini ./
# API·컨트롤러는 기본 의존성만, Worker는 "ml feast", 온라인 추론 Runtime은 "serving" 그룹을 고른다.
ARG SOS_UV_GROUPS=""
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python \
      $(for group in $SOS_UV_GROUPS; do printf -- '--group %s ' "$group"; done)
RUN groupadd --gid 10001 sos && useradd --uid 10001 --gid sos --create-home sos \
    && mkdir -p /var/lib/sos && chown sos:sos /var/lib/sos
USER sos
