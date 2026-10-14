"""Bounded dataset access, profiling and safe Polars feature transforms."""

import hashlib
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

import polars as pl

from .config import Settings
from .object_store import ObjectStore
from .schemas import Transform
from .store import Store

MAX_UPLOAD = 20 * 1024 * 1024
MAX_PREVIEW = 100
ALLOWED_CASTS = {
    "String": pl.String,
    "Int64": pl.Int64,
    "Float64": pl.Float64,
    "Boolean": pl.Boolean,
    "Date": pl.Date,
    "Datetime": pl.Datetime,
}


def save_upload(store: Store, name: str, content: bytes) -> dict:
    """업로드를 검증한 뒤 객체 저장소에 불변 버전으로 등록한다."""
    suffix = Path(name).suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise ValueError("Only CSV and Parquet are supported")
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("File must be nonempty and at most 20 MiB")
    digest = hashlib.sha256(content).hexdigest()
    key = f"datasets/{digest}{suffix}"
    with NamedTemporaryFile(suffix=suffix, dir=store.home / "cache", delete=False) as tmp:
        temporary = Path(tmp.name)
        tmp.write(content)
    try:
        lf = pl.scan_csv(temporary) if suffix == ".csv" else pl.scan_parquet(temporary)
        if not lf.collect_schema().names():
            raise ValueError("Dataset has no columns")
        location = ObjectStore().location(key)
        ObjectStore().put(key, temporary)
    finally:
        temporary.unlink(missing_ok=True)
    return store.register_dataset(name=Path(name).name, kind="file",
                                  location=location, version=digest)


def iceberg_catalog(store: Store):
    """Catalog 메타데이터와 실제 Parquet 파일 저장 책임을 분리한다."""
    from pyiceberg.catalog.sql import SqlCatalog

    config = Settings.from_env()
    return SqlCatalog(
        "sos",
        uri=store.database_url,
        warehouse=f"s3://{config.s3_bucket}/iceberg",
        **{
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
            "s3.endpoint": config.s3_endpoint,
            "s3.access-key-id": config.s3_access_key,
            "s3.secret-access-key": config.s3_secret_key,
            "s3.region": config.s3_region,
            "s3.force-virtual-addressing": "false",
        },
    )


def import_to_iceberg(store: Store, dataset: dict, table_name: str) -> dict:
    """비용이 큰 데이터 변환은 API가 아닌 Worker 프로세스에서 실행한다."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name):
        raise ValueError("Invalid Iceberg table name")
    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    ident = f"default.{table_name}"
    arrow = scan_dataset(store, dataset).collect().to_arrow()
    try:
        table = catalog.load_table(ident)
    except Exception as exc:
        from pyiceberg.exceptions import NoSuchTableError
        if not isinstance(exc, NoSuchTableError):
            raise
        table = catalog.create_table(ident, schema=arrow.schema)
    table.append(arrow)
    snapshot = catalog.load_table(ident).current_snapshot()
    if snapshot is None:
        raise ValueError("Iceberg append did not create a snapshot")
    return {"name": ident, "kind": "iceberg", "location": ident,
            "version": str(snapshot.snapshot_id)}


def connect_iceberg(store: Store, table_name: str, snapshot_id: str | None = None) -> dict:
    """기존 테이블의 지정 Snapshot을 Dataset으로 등록한다."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name):
        raise ValueError("Invalid Iceberg table name")
    from pyiceberg.exceptions import NoSuchTableError

    try:
        table = iceberg_catalog(store).load_table(f"default.{table_name}")
    except NoSuchTableError as exc:
        raise ValueError("Iceberg table does not exist") from exc
    snapshots = {str(s.snapshot_id) for s in table.snapshots()}
    if snapshot_id is None:
        current = table.current_snapshot()
        if current is None:
            raise ValueError("Iceberg table has no data snapshot")
        snapshot_id = str(current.snapshot_id)
    if snapshot_id not in snapshots:
        raise ValueError("Iceberg snapshot is not available; it may have expired")
    return store.register_dataset(name=f"default.{table_name}", kind="iceberg",
                                  location=f"default.{table_name}", version=snapshot_id)


def iceberg_snapshots(store: Store, table_name: str) -> list[dict]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name):
        raise ValueError("Invalid Iceberg table name")
    table = iceberg_catalog(store).load_table(f"default.{table_name}")
    return [
        {"snapshot_id": str(s.snapshot_id), "timestamp_ms": s.timestamp_ms}
        for s in table.snapshots()
    ]


def scan_dataset(store: Store, dataset: dict) -> pl.LazyFrame:
    if dataset["kind"] == "file":
        storage = ObjectStore()
        key = storage.key(dataset["location"])
        suffix = Path(key).suffix
        destination = (store.home / "cache" / f"{dataset['version']}{suffix}").resolve()
        if not destination.is_relative_to(store.home / "cache"):
            raise ValueError("Dataset cache path is invalid")
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != dataset["version"]:
            storage.get(key, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != dataset["version"]:
                destination.unlink(missing_ok=True)
                raise ValueError("Dataset content hash does not match the registered version")
        return pl.scan_csv(destination, try_parse_dates=True) if suffix == ".csv" else pl.scan_parquet(destination)
    if dataset["kind"] == "iceberg":
        table = iceberg_catalog(store).load_table(dataset["location"])
        snapshot = int(dataset["version"])
        if snapshot not in {s.snapshot_id for s in table.snapshots()}:
            raise ValueError("Iceberg snapshot is not available; it may have expired")
        return pl.scan_iceberg(table, snapshot_id=snapshot)
    raise ValueError("Unknown dataset type")


def safe_preview(lf: pl.LazyFrame, limit: int = 30) -> dict:
    if not 1 <= limit <= MAX_PREVIEW:
        raise ValueError("Preview limit must be between 1 and 100")
    frame = lf.limit(limit).collect()
    return {
        "schema": {c: str(t) for c, t in lf.collect_schema().items()},
        "rows": frame.to_dicts(),
        "returned_rows": frame.height,
    }


def profile(lf: pl.LazyFrame, sample: int | None = 1000) -> dict:
    """Compute bounded or full statistics without collecting every row in memory."""
    if sample is not None and not 1 <= sample <= 10000:
        raise ValueError("Sample size must be between 1 and 10000")
    selected = lf.head(sample) if sample is not None else lf
    schema = selected.collect_schema()
    aggregations = [pl.len().alias("__rows")]
    for number, (name, dtype) in enumerate(schema.items()):
        col = pl.col(name)
        key = f"c{number}_"
        aggregations.extend(
            [
                col.null_count().alias(key + "null"),
                col.n_unique().alias(key + "unique"),
            ]
        )
        if dtype.is_float():
            aggregations.append(col.is_nan().sum().alias(key + "nan"))
        if dtype.is_numeric():
            aggregations.extend(
                [
                    col.min().alias(key + "min"),
                    col.max().alias(key + "max"),
                    col.mean().alias(key + "mean"),
                ]
            )
    values = selected.select(aggregations).collect().to_dicts()[0]
    columns = []
    for number, (name, dtype) in enumerate(schema.items()):
        key = f"c{number}_"
        info = {
            "name": name,
            "dtype": str(dtype),
            "nulls": values[key + "null"],
            "nan_count": values[key + "nan"] if dtype.is_float() else None,
            "unique": values[key + "unique"],
        }
        if dtype.is_numeric():
            info.update({k: values[key + k] for k in ("min", "max", "mean")})
        columns.append(info)
    return {
        "sampled": sample is not None,
        "sample_limit": sample,
        "row_count": values["__rows"],
        "columns": columns,
    }


def transform(lf: pl.LazyFrame, spec: Transform) -> pl.LazyFrame:
    if spec.order_by:
        if spec.order_by not in lf.collect_schema():
            raise ValueError("Time/order column does not exist")
        lf = lf.sort(spec.order_by)
    for step in spec.steps:
        columns = lf.collect_schema().names()
        if step.op == "select":
            if not set(step.columns or []).issubset(columns):
                raise ValueError("Selected column does not exist")
            lf = lf.select(step.columns)
            continue
        if step.column not in columns:
            raise ValueError(f"Unknown column: {step.column}")
        col = pl.col(step.column)
        if step.op == "filter_not_null":
            lf = lf.filter(col.is_not_null())
        elif step.op == "fill_null":
            lf = lf.with_columns(col.fill_null(step.value))
        elif step.op == "cast":
            lf = lf.with_columns(col.cast(ALLOWED_CASTS[step.dtype], strict=True))
        elif step.op in ("lag", "rolling_mean"):
            if not spec.order_by:
                raise ValueError("Time-based features require order_by")
            alias = step.alias or f"{step.column}_{step.op}"
            if alias in columns:
                raise ValueError("Feature output column already exists")
            if step.op == "lag":
                lf = lf.with_columns(col.shift(step.periods).alias(alias))
            else:
                lf = lf.with_columns(col.rolling_mean(step.window).alias(alias))
    return lf
