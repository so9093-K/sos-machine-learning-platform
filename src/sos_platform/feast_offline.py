"""Feast DuckDB/Ibis Offline Retrieval의 물리 실행.

플랫폼 계약(등록 정의·Point-in-Time 조회 의미·Dataset·Job)과 물리 구현을 구분한다. Point-in-Time Join은
Entity 행마다 독립적으로 정해지므로, Entity를 배치로 나누어 Feast에 넘기고 결과를 배치별 조각으로 쓴 뒤 하나의
Parquet으로 합친다. 배치 크기는 실행 자원에 맞춘 물리 설정이며 조회할 수 있는 Entity 수의 한도가 아니다.
DuckDB는 Attempt Stage로 Spill한다. 새 Offline Store는 이 경계에서만 조회·산출 방식을 바꾼다.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .config import Settings
from .duckdb_spill import SpillBudget, directory_bytes, stage_exhausted
from .errors import PlatformError

_MIB = 1024 * 1024


def entity_batch_rows() -> int:
    """한 번의 Feast 조회에 넘길 Entity 행 수(물리 배치 크기)."""
    raw = os.environ.get("SOS_FEAST_ENTITY_BATCH_ROWS", "100000")
    try:
        rows = int(raw)
    except ValueError as exc:
        raise ValueError("SOS_FEAST_ENTITY_BATCH_ROWS must be a positive integer") from exc
    if rows < 1:
        raise ValueError("SOS_FEAST_ENTITY_BATCH_ROWS must be positive")
    return rows


def retrieve_to_parquet(
    feast,
    service,
    *,
    entities: Path,
    expected_columns: list[str],
    timestamp_column: str,
    output: Path,
    budget: SpillBudget,
    batch_rows: int,
) -> int:
    """Stage의 Entity Parquet을 배치로 조회해 SOS 불변 게시 경계의 Parquet로 내보낸다.

    Args:
        feast: 등록 정의를 검증한 FeatureStore.
        service: 등록 Feature Service.
        entities: Entity 키·시각(·관측 식별자) Parquet. 시각 컬럼은 `timestamp_column` 이름이다.
        expected_columns: 결과에서 고를 컬럼(`event_timestamp` 포함).
        timestamp_column: 결과에서 `event_timestamp`를 되돌릴 Entity 시각 컬럼 이름.
        output: 게시할 결과 Parquet.
        budget: DuckDB Spill과 배치 조각을 쓰는 Attempt Stage·자원 한도.
        batch_rows: 한 번의 Feast 조회에 넘길 Entity 행 수.

    Returns:
        결과 행 수(= Entity 행 수).

    Raises:
        ValueError: Feast 결과의 컬럼·행 수가 Entity와 맞지 않는다.
        PlatformError: Stage 공간이나 메모리로 Point-in-Time Join을 수용할 수 없다.
    """
    import ibis
    import pandas as pd

    settings = Settings.from_env()
    endpoint = urlsplit(settings.s3_endpoint)
    if (
        endpoint.scheme not in {"http", "https"} or not endpoint.hostname
        or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment
        or endpoint.username or endpoint.password
    ):
        raise ValueError("Feast S3 endpoint must be a plain HTTP(S) host")
    used = budget.used_bytes() if budget.used_bytes is not None else directory_bytes(budget.stage)
    remaining = budget.max_stage_bytes - used
    if remaining < _MIB:
        raise PlatformError(budget.stage_error)
    parts = budget.stage / "retrieved"
    parts.mkdir()
    previous = ibis.options.default_backend
    written = 0
    try:
        ibis.options.default_backend = ibis.duckdb.connect(
            s3_endpoint=endpoint.netloc,
            s3_use_ssl=(endpoint.scheme == "https"),
            s3_url_style="path",
            s3_region=settings.s3_region,
            s3_access_key_id=settings.s3_access_key,
            s3_secret_access_key=settings.s3_secret_key,
            memory_limit=f"{max(128, min(1024, budget.memory_mib // 4))}MiB",
            temp_directory=str(budget.stage / "spill"),
            max_temp_directory_size=f"{remaining // _MIB}MiB",
        )
        with pq.ParquetFile(entities) as reader:
            for number, batch in enumerate(reader.iter_batches(batch_size=batch_rows)):
                entity_rows = batch.to_pandas().rename(
                    columns={timestamp_column: "event_timestamp"}
                )
                entity_rows["event_timestamp"] = pd.to_datetime(
                    entity_rows["event_timestamp"], utc=True
                )
                result = feast.get_historical_features(
                    entity_df=entity_rows, features=service,
                ).to_df()
                if any(name not in result.columns for name in expected_columns):
                    raise ValueError("Feast returned an incompatible Feature schema")
                if len(result) != len(entity_rows):
                    raise ValueError("Feast returned a different Entity row count")
                table = pa.Table.from_pandas(
                    result[expected_columns].rename(columns={"event_timestamp": timestamp_column}),
                    preserve_index=False,
                )
                pq.write_table(table, parts / f"{number:08d}.parquet")
                written += len(result)
    except (duckdb.OutOfMemoryException, duckdb.IOException) as exc:
        if stage_exhausted(exc):
            raise PlatformError(budget.stage_error) from exc
        if isinstance(exc, duckdb.OutOfMemoryException):
            raise PlatformError(budget.capacity_error) from exc
        raise
    finally:
        ibis.options.default_backend = previous
    _combine(sorted(parts.iterdir()), output)
    return written


def _combine(paths: list[Path], output: Path) -> None:
    """배치 조각을 하나의 Parquet으로 잇는다. 한 배치에서 값이 모두 없어 타입이 비어 있던 컬럼은
    다른 배치의 타입으로 맞춘다."""
    schema = pa.unify_schemas(
        [pq.read_schema(path).remove_metadata() for path in paths], promote_options="default",
    )
    with pq.ParquetWriter(output, schema) as writer:
        for path in paths:
            with pq.ParquetFile(path) as reader:
                for batch in reader.iter_batches(batch_size=65_536):
                    writer.write_table(
                        pa.Table.from_batches([batch]).replace_schema_metadata(None).cast(schema)
                    )
