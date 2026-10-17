"""불변 Dataset의 등록·조회와 Iceberg 적재를 연결한다."""

import os
import re
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
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
    store: Store, table_name: str, owner_actor: str, *, catalog=None
) -> tuple[object | None, dict]:
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
        if (
            claim is None
            or claim["owner_actor"] != owner_actor
            or table.metadata.properties.get("sos.write-token") != claim["token"]
        ):
            raise PermissionError("Iceberg table write access denied")
        store.activate_iceberg_table(table_name, owner_actor, claim["token"])
    else:
        claim = store.reserve_iceberg_table(table_name, owner_actor)
        if claim["state"] != "Reserved":
            raise PermissionError("Previously owned Iceberg table is missing")
    return table, claim


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
    table, claim = reserve_iceberg_write_target(
        store, table_name, owner_actor, catalog=catalog
    )
    # Partitioned append는 Reader 기반 쓰기가 지원되지 않으므로
    # 이전 호환 경로의 제한을 먼저 검증해 불필요한 대형 스테이징을 피한다.
    if table is not None and table.spec().fields:
        from .processing import bounded_iceberg_arrow

        with pinned_scan_dataset(store, dataset) as source:
            data = bounded_iceberg_arrow(source)
        if on_commit_start is not None:
            on_commit_start()
        table.append(data, snapshot_properties={"sos.attempt-id": attempt_id})
    else:
        # Attempt 식별자를 경로에 포함해 강제 종료 후 부모가 자기 임시 파일만 제거한다.
        scratch = store.home / "cache" / "iceberg-staging"
        scratch.mkdir(parents=True, exist_ok=True)
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
                    store.activate_iceberg_table(table_name, owner_actor, claim["token"])
                reader = pa.RecordBatchReader.from_batches(
                    parquet.schema_arrow, parquet.iter_batches(batch_size=65_536)
                )
                try:
                    if on_commit_start is not None:
                        on_commit_start()
                    table.append(reader, snapshot_properties={"sos.attempt-id": attempt_id})
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
        raise InvalidRequestError("Invalid Iceberg table name")
    from pyiceberg.exceptions import NoSuchTableError

    try:
        table = iceberg_catalog(store).load_table(f"default.{table_name}")
    except NoSuchTableError as exc:
        raise InvalidRequestError("Iceberg table does not exist") from exc
    snapshots = {str(s.snapshot_id) for s in table.snapshots()}
    if snapshot_id is None:
        current = table.current_snapshot()
        if current is None:
            raise InvalidRequestError("Iceberg table has no data snapshot")
        snapshot_id = str(current.snapshot_id)
    if snapshot_id not in snapshots:
        raise InvalidRequestError("Iceberg snapshot is not available; it may have expired")
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
        raise InvalidRequestError("Invalid Iceberg table name")
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
            raise InvalidRequestError("Iceberg snapshot is not available; it may have expired")
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
