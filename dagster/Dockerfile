# Dagster Runtime을 SOS API·Worker 잠금 환경과 분리한다.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    SOS_HOME=/var/lib/sos \
    DAGSTER_HOME=/app/dagster
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .python-version ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY dagster/ ./dagster/
RUN python -m pip install --no-cache-dir . -r dagster/requirements.txt
RUN groupadd --gid 10001 sos && useradd --uid 10001 --gid sos --create-home sos \
    && mkdir -p /var/lib/sos && chown -R sos:sos /var/lib/sos /app/dagster
USER sos
WORKDIR /app
