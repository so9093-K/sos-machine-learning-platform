# Dagster Runtime을 SOS API·Worker 잠금 환경과 분리한다.
FROM ghcr.io/astral-sh/uv:0.9.2 AS uv
# SOS 이미지와 같은 고정 Python 기반을 사용한다.
FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1 \
    SOS_HOME=/var/lib/sos \
    DAGSTER_HOME=/app/dagster
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock .python-version ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY dagster/ ./dagster/
RUN uv sync --locked --no-dev --group dagster --no-editable --python /usr/local/bin/python
RUN groupadd --gid 10001 sos && useradd --uid 10001 --gid sos --create-home sos \
    && mkdir -p /var/lib/sos /var/lib/dagster/logs /var/lib/dagster/data \
    && chown -R sos:sos /var/lib/sos /var/lib/dagster /app/dagster
USER sos
WORKDIR /app
