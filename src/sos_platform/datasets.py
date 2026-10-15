"""불변 데이터 조회, EDA 프로파일 및 Polars 피처 변환 기능."""

import hashlib
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

import polars as pl

from .config import Settings
from .object_store import ObjectStore
from .store import Store

MAX_UPLOAD = 20 * 1024 * 1024


def file_digest(path: Path) -> str:
    """큰 로컬 캐시 파일도 전체 내용을 메모리에 올리지 않고 검증한다."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_upload(store: Store, name: str, content: bytes, *, owner_actor: str | None = None) -> dict:
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
        lf = (
            pl.scan_csv(temporary, try_parse_dates=True)
            if suffix == ".csv"
            else pl.scan_parquet(temporary)
        )
        if not lf.collect_schema().names():
            raise ValueError("Dataset has no columns")
        storage = ObjectStore()
        location = storage.location(key)
        write_id = store.begin_object_write(
            location=location, owner_kind="upload", owner_id=str(uuid4())
        )
        try:
            storage.put(key, temporary)
            return store.register_dataset(
                name=Path(name).name,
                kind="file",
                location=location,
                version=digest,
                write_id=write_id,
                owner_actor=owner_actor,
            )
        except Exception as exc:
            try:
                store.fail_object_write(write_id, str(exc))
            except Exception:
                pass
            raise
    finally:
        temporary.unlink(missing_ok=True)


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


def import_to_iceberg(store: Store, dataset: dict, table_name: str, attempt_id: str) -> dict:
    """비용이 큰 데이터 변환은 API가 아닌 Worker 프로세스에서 실행한다."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name):
        raise ValueError("Invalid Iceberg table name")
    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    ident = f"default.{table_name}"
    # 단일 append의 메모리·행 수 한도를 강제한다. 추후 배치 적재 계약과 구분한다.
    from .processing import bounded_iceberg_arrow

    arrow = bounded_iceberg_arrow(scan_dataset(store, dataset))
    try:
        table = catalog.load_table(ident)
    except Exception as exc:
        from pyiceberg.exceptions import NoSuchTableError

        if not isinstance(exc, NoSuchTableError):
            raise
        table = catalog.create_table(ident, schema=arrow.schema)
    # 동일 테이블에 외부 작성자가 개입하더라도 최신 Snapshot을 오인하지 않는다.
    table.append(arrow, snapshot_properties={"sos.attempt-id": attempt_id})
    snapshots = [
        snapshot for snapshot in catalog.load_table(ident).snapshots()
        if snapshot.summary and snapshot.summary.get("sos.attempt-id") == attempt_id
    ]
    if len(snapshots) != 1:
        raise RuntimeError("Unable to identify the committed Iceberg Snapshot")
    snapshot = snapshots[0]
    retention_id = str(uuid4())
    from .retention import ensure_tag

    ensure_tag(store, dataset_id=retention_id, location=ident, snapshot_id=str(snapshot.snapshot_id))
    return {
        "name": ident,
        "kind": "iceberg",
        "location": ident,
        "version": str(snapshot.snapshot_id),
        "retention_id": retention_id,
    }


def connect_iceberg(store: Store, table_name: str, snapshot_id: str | None = None, *, owner_actor: str | None = None) -> dict:
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
    if dataset["kind"] == "file":
        storage = ObjectStore()
        key = storage.key(dataset["location"])
        suffix = Path(key).suffix
        destination = (store.home / "cache" / f"{dataset['version']}{suffix}").resolve()
        if not destination.is_relative_to(store.home / "cache"):
            raise ValueError("Dataset cache path is invalid")
        if not destination.is_file() or file_digest(destination) != dataset["version"]:
            storage.get(key, destination)
            if file_digest(destination) != dataset["version"]:
                destination.unlink(missing_ok=True)
                raise ValueError("Dataset content hash does not match the registered version")
        return (
            pl.scan_csv(destination, try_parse_dates=True)
            if suffix == ".csv"
            else pl.scan_parquet(destination)
        )
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
