# Scalar는 빌드 시에만 Node를 사용한다. Python 실행 이미지에는 번들만 복사한다.
FROM node:24-bookworm-slim AS scalar-assets
WORKDIR /build
COPY tools/scalar-docs/ ./tools/scalar-docs/
COPY scripts/build_scalar_assets.sh ./scripts/
RUN bash scripts/build_scalar_assets.sh

# API, Worker, 초기화 명령은 같은 잠금 파일 기반 Python 이미지를 사용한다.
FROM ghcr.io/astral-sh/uv:0.9.2 AS uv
FROM python:3.12-slim-bookworm

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
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python
RUN groupadd --gid 10001 sos && useradd --uid 10001 --gid sos --create-home sos \
    && mkdir -p /var/lib/sos && chown sos:sos /var/lib/sos
USER sos
