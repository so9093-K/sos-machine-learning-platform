"""S3의 불변 Dataset 로컬 캐시: 읽기 Lease, 할당량, 안전한 축출.

파일별 PostgreSQL advisory shared lock을 읽기 Lease로 사용한다.
다운로드 및 축출은 exclusive lock을 사용해 읽는 중인 파일의 삭제를 방지한다.
"""

import hashlib
import os
import re
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from sqlalchemy import text

from .errors import PlatformError, RuntimeConfigurationError
from .object_store import ObjectStore
from .store import Store

_CACHE_FILE = re.compile(r"^[0-9a-f]{64}\.(csv|parquet|tsv|txt)$")
_CACHE_PENDING = re.compile(r"^[0-9a-f]{64}\.(csv|parquet|tsv|txt)\.pending-[0-9a-f]{32}$")
# 프로세스 안에서 이미 SHA-256을 검증한 불변 파일의 파일시스템 정체성을 기억한다.
# 다른 프로세스는 처음 읽을 때 별도로 검증한다. 최대 항목 수를 제한한다.
_VERIFIED: OrderedDict[Path, tuple[str, tuple[int, int, int, int, int]]] = OrderedDict()
_VERIFIED_LOCK = Lock()
_MAX_VERIFIED = 256


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    item = path.stat()
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


def _remember(path: Path, version: str, signature: tuple[int, int, int, int, int]) -> None:
    with _VERIFIED_LOCK:
        _VERIFIED[path] = (version, signature)
        _VERIFIED.move_to_end(path)
        if len(_VERIFIED) > _MAX_VERIFIED:
            _VERIFIED.popitem(last=False)


@dataclass(frozen=True)
class CachePolicy:
    """원본은 S3에 보존하고, 재구성 가능한 디스크 캐시에만 상한을 둔다."""

    max_bytes: int = 2048 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "CachePolicy":
        value = os.getenv("SOS_DATASET_CACHE_MIB", str(cls().max_bytes // (1024 * 1024)))
        if not value.isdecimal() or not 256 <= int(value) <= 131072:
            raise RuntimeConfigurationError("SOS_DATASET_CACHE_MIB must be between 256 and 131072")
        return cls(max_bytes=int(value) * 1024 * 1024)


def _slot(version: str) -> int:
    """PostgreSQL advisory lock이 요구하는 signed int32 키로 매핑한다."""
    number = int(version[:8], 16)
    return number - 2**32 if number >= 2**31 else number


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


def _verified_files(root: Path) -> list[Path]:
    return [
        item
        for item in root.iterdir()
        if _CACHE_FILE.fullmatch(item.name) and item.is_file() and not item.is_symlink()
    ]


def _ready(path: Path, version: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    signature = _signature(path)
    with _VERIFIED_LOCK:
        if _VERIFIED.get(path) == (version, signature):
            _VERIFIED.move_to_end(path)
            return True
    # 파일이 변경됐거나 새 프로세스에서 처음 열면 내용 해시를 다시 확인한다.
    if _digest(path) != version:
        return False
    if _signature(path) != signature:
        # 검증 도중 파일이 변경됐다면 결과를 신뢰하지 않는다.
        return False
    _remember(path, version, signature)
    return True


def _download_under_quota(
    store: Store,
    storage: ObjectStore,
    key: str,
    path: Path,
    version: str,
    policy: CachePolicy,
) -> None:
    """배타 원본 Lease를 보유한 상태에서 할당량·축출·검증을 원자화한다."""
    info = storage.client.head_object(Bucket=storage.config.s3_bucket, Key=key)
    required = int(info["ContentLength"])
    if required > policy.max_bytes:
        raise PlatformError("SOS-CACHE-CAPACITY")
    with store.engine.connect() as quota:
        quota.execute(text("SELECT pg_advisory_lock(83419,0)"))
        quota.commit()
        try:
            root = path.parent
            # 이 잠금 아래에서는 SOS 캐시 다운로드가 동시에 진행되지 않는다.
            # 프로세스 사망 후 남은 SOS 고유 임시 파일만 정리하며 원본은 보존한다.
            for item in root.iterdir():
                if _CACHE_PENDING.fullmatch(item.name) and item.is_file() and not item.is_symlink():
                    item.unlink()
            files = [item for item in _verified_files(root) if item != path]
            occupied = sum(item.stat().st_size for item in files)
            # quota lock 아래에서 같은 캐시 공간의 할당을 직렬화한다.
            for candidate in sorted(files, key=lambda item: item.stat().st_mtime):
                if occupied + required <= policy.max_bytes:
                    break
                acquired = quota.execute(
                    text("SELECT pg_try_advisory_lock(83418,:slot)"),
                    {"slot": _slot(candidate.stem)},
                ).scalar_one()
                quota.commit()
                if not acquired:
                    continue
                try:
                    # 다른 요청이 파일을 사용 중이면 exclusive lease를 얻을 수 없다.
                    if candidate.is_file():
                        size = candidate.stat().st_size
                        candidate.unlink()
                        occupied -= size
                finally:
                    quota.execute(
                        text("SELECT pg_advisory_unlock(83418,:slot)"),
                        {"slot": _slot(candidate.stem)},
                    )
                    quota.commit()
            if occupied + required > policy.max_bytes:
                raise PlatformError("SOS-CACHE-CAPACITY")
            storage.get(key, path, expected_sha256=version)
        finally:
            quota.execute(text("SELECT pg_advisory_unlock(83419,0)"))
            quota.commit()


@contextmanager
def cached_dataset_file(
    store: Store, dataset: dict, policy: CachePolicy | None = None
) -> Iterator[Path]:
    """병렬 읽기를 허용하면서 다운로드·축출 중에는 독점 Lease를 적용한다."""
    version = dataset["version"]
    if dataset["kind"] != "file" or not re.fullmatch(r"[0-9a-f]{64}", version):
        raise ValueError("Invalid cache dataset version")
    policy = policy or CachePolicy.from_env()
    storage = ObjectStore()
    key = storage.key(dataset["location"])
    suffix = Path(key).suffix.lower()
    if suffix not in (".csv", ".parquet", ".tsv", ".txt"):
        raise ValueError("Unsupported cache file extension")
    root = store.home / "cache"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{version}{suffix}"
    slot = _slot(version)
    args = {"slot": slot}
    with store.engine.connect() as lease:
        # 읽기 전용 사용자는 서로 막지 않는다.
        lease.execute(text("SELECT pg_advisory_lock_shared(83418,:slot)"), args)
        lease.commit()
        shared = True
        try:
            if not _ready(path, version):
                # 공유 Lease를 잠시 해제하고 다운로드가 끝날 때까지 독점한다.
                lease.execute(text("SELECT pg_advisory_unlock_shared(83418,:slot)"), args)
                lease.commit()
                shared = False
                lease.execute(text("SELECT pg_advisory_lock(83418,:slot)"), args)
                lease.commit()
                try:
                    # 다른 프로세스가 먼저 내려받았을 수 있으므로 재확인한다.
                    if not _ready(path, version):
                        _download_under_quota(store, storage, key, path, version, policy)
                    # exclusive를 해제하기 전에 공유 Lease를 다시 얻는다.
                    lease.execute(text("SELECT pg_advisory_lock_shared(83418,:slot)"), args)
                    lease.commit()
                    shared = True
                finally:
                    lease.execute(text("SELECT pg_advisory_unlock(83418,:slot)"), args)
                    lease.commit()
            # 직전의 _ready() 검사 또는 S3 다운로드의 SHA-256 검증이 성공했다.
            # touch로 바뀐 mtime/ctime을 기록해야 다음 읽기에서 재해시하지 않는다.
            os.utime(path, None)
            _remember(path, version, _signature(path))
            yield path
        finally:
            if shared:
                lease.execute(text("SELECT pg_advisory_unlock_shared(83418,:slot)"), args)
                lease.commit()
