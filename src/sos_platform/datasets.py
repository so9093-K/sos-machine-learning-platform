"""불변 Dataset의 등록·조회와 Iceberg 적재를 연결한다."""

import itertools
import os
import re
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import UUID, uuid4

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from .cache import cached_dataset_file
from .config import Settings
from .errors import InvalidRequestError, PlatformError
from .file_formats import reader_for_suffix, scan_file
from .ingestion import UploadPolicy, staged_upload
from .object_store import ObjectStore
from .store import Store


def save_upload_file(
    store: Store,
    name: str,
    source: BinaryIO,
    *,
    owner_actor: str | None = None,
    max_bytes: int | None = None,
    read_options: dict | None = None,
) -> dict:
    """원본 바이트의 SHA-256과 해석 설정을 별개로 보존해 Dataset을 등록한다."""
    limit = max_bytes if max_bytes is not None else UploadPolicy.from_env().max_bytes
    with staged_upload(source, name, store.home / "cache", max_bytes=limit) as staged:
        reader = reader_for_suffix(staged.suffix)
        options = reader.upload_options(read_options)
        try:
            row_count = reader.inspect_upload(staged.path, options)
            lf = reader.scan_polars(staged.path, options)
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
                parse_options=options,
                row_count=row_count,
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


def _stage_prefix(attempt_id: str) -> str:
    """내부 Attempt UUID를 검증해 경로 제어 문자를 허용하지 않는다."""
    return f"attempt-{UUID(attempt_id)}-"


def iceberg_staging_bytes(store: Store, attempt_id: str) -> int:
    """Attempt 소유 임시 파일 전체를 관찰한다. 엄격한 파일시스템 쿼터는 아니다."""
    root = store.home / "cache" / "iceberg-staging"
    if not root.exists():
        return 0

    def fail_on_unreadable_directory(error: OSError) -> None:
        # 접근 불가 디렉터리를 0바이트로 오인하면 제한을 우회할 수 있다.
        raise error

    total = 0
    for directory in root.glob(_stage_prefix(attempt_id) + "*"):
        if not directory.is_dir() or directory.is_symlink():
            continue
        for parent, _subdirs, filenames in os.walk(
            directory, followlinks=False, onerror=fail_on_unreadable_directory
        ):
            for filename in filenames:
                try:
                    info = (Path(parent) / filename).stat(follow_symlinks=False)
                except FileNotFoundError:
                    # 정상 정리와 관측이 경합할 수 있다.
                    continue
                # 하위 스크래치·Spill 파일도 포함하되 외부 symlink는 따르지 않는다.
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
    return total


def cleanup_iceberg_staging(store: Store, attempt_id: str | None = None) -> None:
    """실행 종료·Worker 재시작 시 불완전한 로컬 입력만 정리한다.

    Args:
        store: 작업 로컬 루트.
        attempt_id: 지정하면 해당 Attempt 임시 파일만 제거한다.
            None은 독점 Worker lock을 획득한 복구 시점에만 사용한다.
    """
    root = store.home / "cache" / "iceberg-staging"
    if not root.exists():
        return
    prefix = _stage_prefix(attempt_id) if attempt_id is not None else "attempt-"
    for path in root.iterdir():
        if not path.name.startswith(prefix):
            continue
        # 임시 디렉터리만 대상으로 하며 symlink는 따라가지 않는다.
        if (
            path.is_dir()
            and not path.is_symlink()
            and re.fullmatch(
                r"attempt-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[A-Za-z0-9_]+",
                path.name,
            )
        ):
            shutil.rmtree(path)


def reserve_iceberg_write_target(
    store: Store,
    table_name: str,
    owner_actor: str,
    *,
    catalog=None,
    reserve_if_missing: bool = True,
) -> tuple[object | None, dict | None]:
    """API와 Worker가 공유하는 실제 Catalog 테이블·쓰기 소유권 검사.

    기존 테이블에 소유 토큰이 없거나 예약된 Active 테이블이 사라졌다면
    다른 테이블을 같은 이름으로 자동 인수하지 않고 쓰기를 거부한다.
    """
    from pyiceberg.exceptions import NoSuchTableError

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table_name) or not owner_actor:
        raise PermissionError("Iceberg table write access denied")
    active_catalog = catalog if catalog is not None else iceberg_catalog(store)
    try:
        table = active_catalog.load_table(f"default.{table_name}")
    except NoSuchTableError:
        table = None
    claim = store.iceberg_write_claim(table_name)
    if table is not None:
        table_uuid = str(table.metadata.table_uuid)
        if (
            claim is None
            or claim["owner_actor"] != owner_actor
            or claim["state"] not in ("Reserved", "Active")
            or (claim["state"] == "Active" and claim["table_uuid"] != table_uuid)
            or (claim["state"] == "Reserved" and claim["table_uuid"] not in (None, table_uuid))
            or table.metadata.properties.get("sos.write-token") != claim["token"]
        ):
            raise PermissionError("Iceberg table write access denied")
        if reserve_if_missing:
            # API의 read-only preflight는 Reserved 상태를 변경하지 않는다.
            # Catalog 커밋을 수행하는 Worker만 동일 토큰·UUID를 활성화한다.
            store.activate_iceberg_table(
                table_name, owner_actor, claim["token"], str(table.metadata.table_uuid)
            )
    else:
        if reserve_if_missing:
            claim = store.reserve_iceberg_table(table_name, owner_actor)
        if claim is not None and (
            claim["owner_actor"] != owner_actor or claim["state"] != "Reserved"
        ):
            raise PermissionError("Previously owned Iceberg table is missing")
    return table, claim


_ICEBERG_CHUNK_BYTES = 128 * 1024 * 1024


def _append_partitioned(table, parquet: pq.ParquetFile, properties: dict[str, str]) -> None:
    """파티션 테이블에 배치마다 데이터 파일을 쓰고 전체를 한 Snapshot으로 커밋한다.

    pyiceberg는 파티션 테이블의 Reader 스트리밍 append를 아직 지원하지 않는다
    (apache/iceberg-python#2152). 공개 append와 같은 단계(스키마 호환 검사 → 파티션별 데이터 파일 →
    한 append Snapshot)를 일정 크기의 배치마다 반복해 입력 크기와 관계없이 메모리를 배치 크기로
    제한한다. 데이터 파일 이름이 겹치지 않도록 한 counter를 공유한다.
    """
    from pyiceberg.io.pyarrow import _check_pyarrow_schema_compatible, _dataframe_to_data_files
    from pyiceberg.table import DOWNCAST_NS_TIMESTAMP_TO_US_ON_WRITE, TableProperties
    from pyiceberg.utils.config import Config
    from pyiceberg.utils.properties import property_as_bool

    transaction = table.transaction()
    metadata = transaction.table_metadata
    _check_pyarrow_schema_compatible(
        metadata.schema(), provided_schema=parquet.schema_arrow,
        downcast_ns_timestamp_to_us=Config().get_bool(DOWNCAST_NS_TIMESTAMP_TO_US_ON_WRITE) or False,
        format_version=metadata.format_version,
    )
    update = transaction.update_snapshot(snapshot_properties=properties)
    merge = property_as_bool(
        metadata.properties, TableProperties.MANIFEST_MERGE_ENABLED,
        TableProperties.MANIFEST_MERGE_ENABLED_DEFAULT,
    )
    counter = itertools.count(0)

    def chunks():
        batches, size = [], 0
        for batch in parquet.iter_batches(batch_size=65_536):
            batches.append(batch)
            size += batch.nbytes
            if size >= _ICEBERG_CHUNK_BYTES:
                yield pa.Table.from_batches(batches, schema=parquet.schema_arrow)
                batches, size = [], 0
        if batches:
            yield pa.Table.from_batches(batches, schema=parquet.schema_arrow)

    with (update.merge_append() if merge else update.fast_append()) as append_files:
        for chunk in chunks():
            for data_file in _dataframe_to_data_files(
                table_metadata=metadata, df=chunk, io=table.io,
                write_uuid=append_files.commit_uuid, counter=counter,
            ):
                append_files.append_data_file(data_file)
    transaction.commit_transaction()


def import_to_iceberg(
    store: Store,
    dataset: dict,
    table_name: str,
    attempt_id: str,
    *,
    owner_actor: str,
    on_commit_start=None,
) -> dict:
    """비용이 큰 데이터 변환은 API가 아닌 Worker 프로세스에서 실행한다."""
    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    ident = f"default.{table_name}"
    table, claim = reserve_iceberg_write_target(store, table_name, owner_actor, catalog=catalog)
    # Attempt 식별자를 경로에 포함해 강제 종료 후 부모가 자기 임시 파일만 제거한다.
    scratch = store.home / "cache" / "iceberg-staging"
    scratch.mkdir(parents=True, exist_ok=True)
    properties = {"sos.attempt-id": attempt_id}
    with TemporaryDirectory(prefix=_stage_prefix(attempt_id), dir=scratch) as directory:
        staged = Path(directory) / "input.parquet"
        with pinned_scan_dataset(store, dataset) as source:
            source.sink_parquet(staged)
        with pq.ParquetFile(staged) as parquet:
            if table is None:
                table = catalog.create_table(
                    ident,
                    schema=parquet.schema_arrow,
                    properties={"sos.write-token": claim["token"]},
                )
                store.activate_iceberg_table(
                    table_name, owner_actor, claim["token"], str(table.metadata.table_uuid)
                )
            if on_commit_start is not None:
                on_commit_start()
            if table.spec().fields:
                _append_partitioned(table, parquet, properties)
            else:
                reader = pa.RecordBatchReader.from_batches(
                    parquet.schema_arrow, parquet.iter_batches(batch_size=65_536)
                )
                try:
                    table.append(reader, snapshot_properties=properties)
                finally:
                    reader.close()
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
        raise InvalidRequestError("Iceberg 테이블 이름 형식이 올바르지 않습니다.")
    from pyiceberg.exceptions import NoSuchTableError

    try:
        table = iceberg_catalog(store).load_table(f"default.{table_name}")
    except NoSuchTableError as exc:
        raise InvalidRequestError(
            "Iceberg 테이블을 찾을 수 없습니다. 테이블 이름을 확인하세요."
        ) from exc
    snapshots = {str(s.snapshot_id) for s in table.snapshots()}
    if snapshot_id is None:
        current = table.current_snapshot()
        if current is None:
            raise InvalidRequestError("Iceberg 테이블에 데이터 Snapshot이 없습니다.")
        snapshot_id = str(current.snapshot_id)
    if snapshot_id not in snapshots:
        raise InvalidRequestError(
            "Iceberg Snapshot을 사용할 수 없습니다. 만료됐을 수 있으니 Snapshot 목록을 다시 조회하세요."
        )
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
        raise InvalidRequestError("Iceberg 테이블 이름 형식이 올바르지 않습니다.")
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
            raise InvalidRequestError(
                "Iceberg Snapshot을 사용할 수 없습니다. 만료됐을 수 있으니 Snapshot 목록을 다시 조회하세요."
            )
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
            yield scan_file(path, dataset)
    else:
        yield scan_dataset(store, dataset)
