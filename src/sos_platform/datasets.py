"""불변 Dataset의 등록·조회와 Iceberg 적재를 연결한다."""

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from .cache import cached_dataset_file
from .config import Settings
from .errors import PlatformError
from .ingestion import UploadPolicy, staged_upload
from .object_store import ObjectStore
from .store import Store


def save_upload(store: Store, name: str, content: bytes, *, owner_actor: str | None = None) -> dict:
    """합성 예제 등 기존 바이트 입력도 동일한 파일 등록 계약으로 처리한다."""
    return save_upload_file(store, name, BytesIO(content), owner_actor=owner_actor)


def save_upload_file(
    store: Store,
    name: str,
    source: BinaryIO,
    *,
    owner_actor: str | None = None,
    max_bytes: int | None = None,
) -> dict:
    """파일 입력을 검증하고 SHA-256 불변 원본과 Dataset 참조를 게시한다."""
    limit = max_bytes if max_bytes is not None else UploadPolicy.from_env().max_bytes
    with staged_upload(source, name, store.home / "cache", max_bytes=limit) as staged:
        try:
            lf = (
                pl.scan_csv(staged.path, try_parse_dates=True)
                if staged.suffix == ".csv"
                else pl.scan_parquet(staged.path)
            )
            if not lf.collect_schema().names():
                raise PlatformError("SOS-UPLOAD-INVALID")
        except (pl.exceptions.PolarsError, OSError, UnicodeError, ValueError) as exc:
            raise PlatformError("SOS-UPLOAD-INVALID") from exc

        key = f"datasets/{staged.sha256}{staged.suffix}"
        storage = ObjectStore()
        location = storage.location(key)
        write_id = store.begin_object_write(
            location=location, owner_kind="upload", owner_id=str(uuid4())
        )
        try:
            storage.put(key, staged.path)
            return store.register_dataset(
                name=Path(name).name,
                kind="file",
                location=location,
                version=staged.sha256,
                write_id=write_id,
                owner_actor=owner_actor,
            )
        except Exception as exc:
            try:
                store.fail_object_write(write_id, str(exc))
            except Exception:
                pass
            raise


def iceberg_catalog(store: Store):
    """Catalog 메타데이터와 실제 Parquet 파일 저장 책임을 분리한다."""
    from pyiceberg.catalog.sql import SqlCatalog

    config = Settings.from_env()
    # 일부 Arrow 버전이 Catalog 설정과 별개로 기본 AWS 자격 증명을 조회한다.
    os.environ.setdefault("AWS_ACCESS_KEY_ID", config.s3_access_key)
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", config.s3_secret_key)
    os.environ.setdefault("AWS_DEFAULT_REGION", config.s3_region)
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
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


def import_to_iceberg(
    store: Store,
    dataset: dict,
    table_name: str,
    attempt_id: str,
    *,
    on_commit_start=None,
) -> dict:
    """비용이 큰 데이터 변환은 API가 아닌 Worker 프로세스에서 실행한다."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name):
        raise ValueError("Invalid Iceberg table name")
    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    ident = f"default.{table_name}"
    # Lazy 결과를 로컬 불변 스테이징 Parquet으로 기록한다. 입력 전체를
    # 메모리에 수집하지 않으며 중간 계산 실패는 Catalog 커밋 이전에 발생한다.
    scratch = store.home / "cache" / "iceberg-staging"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="attempt-", dir=scratch) as directory:
        staged = Path(directory) / "input.parquet"
        with pinned_scan_dataset(store, dataset) as source:
            source.sink_parquet(staged)
        staged_parquet = pq.ParquetFile(staged)
        try:
            table = catalog.load_table(ident)
        except Exception as exc:
            from pyiceberg.exceptions import NoSuchTableError

            if not isinstance(exc, NoSuchTableError):
                raise
            table = catalog.create_table(ident, schema=staged_parquet.schema_arrow)

        if table.spec().fields:
            # 기존 파티션 테이블은 PyIceberg 0.12의 Reader append 대상이 아니다.
            # 이전 작은 입력의 호환 경로를 유지하며 부분 커밋은 수행하지 않는다.
            from .processing import bounded_iceberg_arrow

            with pinned_scan_dataset(store, dataset) as source:
                data = bounded_iceberg_arrow(source)
        else:
            data = pa.RecordBatchReader.from_batches(
                staged_parquet.schema_arrow,
                staged_parquet.iter_batches(batch_size=65_536),
            )

        # 실제 외부 커밋 진입 여부를 부모 Worker에 명시하고 한 Snapshot에 쓰기.
        if on_commit_start is not None:
            on_commit_start()
        try:
            table.append(data, snapshot_properties={"sos.attempt-id": attempt_id})
        finally:
            if isinstance(data, pa.RecordBatchReader):
                data.close()
    snapshots = [
        snapshot
        for snapshot in catalog.load_table(ident).snapshots()
        if snapshot.summary and snapshot.summary.get("sos.attempt-id") == attempt_id
    ]
    if len(snapshots) != 1:
        raise RuntimeError("Unable to identify the committed Iceberg Snapshot")
    snapshot = snapshots[0]
    retention_id = str(uuid4())
    from .retention import ensure_tag

    ensure_tag(
        store, dataset_id=retention_id, location=ident, snapshot_id=str(snapshot.snapshot_id)
    )
    return {
        "name": ident,
        "kind": "iceberg",
        "location": ident,
        "version": str(snapshot.snapshot_id),
        "retention_id": retention_id,
    }


def connect_iceberg(
    store: Store, table_name: str, snapshot_id: str | None = None, *, owner_actor: str | None = None
) -> dict:
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
    from .retention import ensure_tag

    dataset_id = str(uuid4())
    retention_id = ensure_tag(
        store, dataset_id=dataset_id, location=f"default.{table_name}", snapshot_id=snapshot_id
    )
    return store.register_dataset(
        name=f"default.{table_name}",
        kind="iceberg",
        location=f"default.{table_name}",
        version=snapshot_id,
        dataset_id=dataset_id,
        retention_id=retention_id,
        owner_actor=owner_actor,
    )


def iceberg_snapshots(store: Store, table_name: str) -> list[dict]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name):
        raise ValueError("Invalid Iceberg table name")
    table = iceberg_catalog(store).load_table(f"default.{table_name}")
    return [
        {"snapshot_id": str(s.snapshot_id), "timestamp_ms": s.timestamp_ms}
        for s in table.snapshots()
    ]


def scan_dataset(store: Store, dataset: dict) -> pl.LazyFrame:
    """Iceberg LazyFrame만 직접 생성한다. 파일은 반드시 읽기 Lease 안에서 스캔한다."""
    if dataset["kind"] == "file":
        raise RuntimeError("File Dataset scan requires pinned_scan_dataset")
    if dataset["kind"] == "iceberg":
        table = iceberg_catalog(store).load_table(dataset["location"])
        snapshot = int(dataset["version"])
        if snapshot not in {s.snapshot_id for s in table.snapshots()}:
            raise ValueError("Iceberg snapshot is not available; it may have expired")
        config = Settings.from_env()
        return pl.scan_iceberg(
            table,
            snapshot_id=snapshot,
            storage_options={
                "s3.endpoint": config.s3_endpoint,
                "s3.access-key-id": config.s3_access_key,
                "s3.secret-access-key": config.s3_secret_key,
                "s3.region": config.s3_region,
                "s3.force-virtual-addressing": "false",
            },
        )
    raise ValueError("Unknown dataset type")


@contextmanager
def pinned_scan_dataset(store: Store, dataset: dict) -> Iterator[pl.LazyFrame]:
    """LazyFrame가 실제로 계산되는 동안 원본 캐시를 축출로부터 보호한다."""
    if dataset["kind"] == "file":
        with cached_dataset_file(store, dataset) as path:
            yield (
                pl.scan_csv(path, try_parse_dates=True)
                if path.suffix.lower() == ".csv"
                else pl.scan_parquet(path)
            )
    else:
        yield scan_dataset(store, dataset)
