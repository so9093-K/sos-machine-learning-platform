"""Feast DuckDB/Ibis Offline Retrieval의 현재 실행 정책.

플랫폼 계약(등록 정의·Dataset·Job)과 물리 구현(.to_df 및 S3 Backend)을 구분한다.
새로운 Feast Offline Store는 이 구현 경계에서만 조회·산출 방식을 변경한다.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from .config import Settings


def entity_execution_limit() -> int:
    """현재 메모리 물질화 Runtime에 적용되는 명시적인 자원 정책."""
    raw = os.environ.get("SOS_FEAST_MAX_ENTITY_ROWS", "100000")
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("SOS_FEAST_MAX_ENTITY_ROWS must be a positive integer") from exc
    if limit < 1:
        raise ValueError("SOS_FEAST_MAX_ENTITY_ROWS must be positive")
    return limit


def retrieve_to_parquet(
    feast,
    service,
    *,
    entity_rows,
    expected_columns: list[str],
    timestamp_column: str,
    output: Path,
) -> int:
    """Feast 0.67 DuckDB 결과를 기존 SOS 불변 게시 경계의 Parquet로 내보낸다.

    이 Store의 .to_df()는 메모리 물질화 방식이므로 실행 예산은 부모 Worker에서
    강제한다. 다른 Offline Runtime이 이 방식을 공유한다고 가정하지 않는다.
    """
    import ibis

    settings = Settings.from_env()
    endpoint = urlsplit(settings.s3_endpoint)
    if (
        endpoint.scheme not in {"http", "https"} or not endpoint.hostname
        or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment
        or endpoint.username or endpoint.password
    ):
        raise ValueError("Feast S3 endpoint must be a plain HTTP(S) host")
    previous = ibis.options.default_backend
    try:
        ibis.options.default_backend = ibis.duckdb.connect(
            s3_endpoint=endpoint.netloc,
            s3_use_ssl=(endpoint.scheme == "https"),
            s3_url_style="path",
            s3_region=settings.s3_region,
            s3_access_key_id=settings.s3_access_key,
            s3_secret_access_key=settings.s3_secret_key,
        )
        result = feast.get_historical_features(
            entity_df=entity_rows, features=service,
        ).to_df()
    finally:
        ibis.options.default_backend = previous

    if any(name not in result.columns for name in expected_columns):
        raise ValueError("Feast returned an incompatible Feature schema")
    if len(result) != len(entity_rows):
        raise ValueError("Feast returned a different Entity row count")
    result[expected_columns].rename(
        columns={"event_timestamp": timestamp_column}
    ).to_parquet(output, index=False)
    return len(result)
