"""불변 Dataset 캐시의 할당량·사용 중 보호·안전한 축출.

PostgreSQL advisory lock을 파일별 읽기 Lease로 사용한다. 축출 작업은 다른
요청에서 사용 중인 경로를 삭제하지 않으며 원본 S3는 변경하지 않는다.
"""

import hashlib
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from .errors import PlatformError
from .object_store import ObjectStore
from .store import Store

_CACHE_FILE = re.compile(r"^[0-9a-f]{64}\.(csv|parquet)$")


@dataclass(frozen=True)
class CachePolicy:
    max_bytes: int = 2048 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "CachePolicy":
        value = os.getenv("SOS_DATASET_CACHE_MIB", "2048")
        if not value.isdecimal() or not 256 <= int(value) <= 131072:
            raise ValueError("SOS_DATASET_CACHE_MIB must be between 256 and 131072")
        return cls(max_bytes=int(value) * 1024 * 1024)


def _slot(version: str) -> int:
    number = int(version[:8], 16)
    return number - 2**32 if number >= 2**31 else number


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


def _verified_files(root: Path) -> list[Path]:
    return [item for item in root.iterdir()
            if item.is_file() and _CACHE_FILE.fullmatch(item.name)]


@contextmanager
def cached_dataset_file(
    store: Store, dataset: dict, policy: CachePolicy | None = None
) -> Iterator[Path]:
    """읽는 동안 파일 Lease를 유지하고 용량 초과 시 미사용 캐시만 축출한다."""
    if dataset["kind"] != "file" or not re.fullmatch(r"[0-9a-f]{64}", dataset["version"]):
        raise ValueError("Invalid cache dataset version")
    policy = policy or CachePolicy.from_env()
    storage = ObjectStore()
    key = storage.key(dataset["location"])
    suffix = Path(key).suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise ValueError("Unsupported cache file extension")
    cache = store.home / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{dataset['version']}{suffix}"
    slot = _slot(dataset["version"])
    # 같은 원본의 다운로드·읽기·축출은 DB 세션 잠금으로 직렬화한다.
    with store.engine.connect() as lease:
        lease.execute(text("SELECT pg_advisory_lock(83418, :slot)"), {"slot": slot})
        lease.commit()
        try:
            if not path.is_file() or _digest(path) != dataset["version"]:
                info = storage.client.head_object(Bucket=storage.config.s3_bucket, Key=key)
                required = int(info["ContentLength"])
                if required > policy.max_bytes:
                    raise PlatformError("SOS-CACHE-CAPACITY")
                # 동시 캐시 파일 추가·축출의 총량 판단도 별도 세션에서 직렬화한다.
                with store.engine.connect() as quota:
                    quota.execute(text("SELECT pg_advisory_lock(83419,0)"))
                    quota.commit()
                    try:
                        files = [f for f in _verified_files(cache) if f != path]
                        occupied = sum(f.stat().st_size for f in files)
                        for candidate in sorted(files, key=lambda f: f.stat().st_mtime):
                            if occupied + required <= policy.max_bytes:
                                break
                            victim_slot = _slot(candidate.stem)
                            acquired = quota.execute(
                                text("SELECT pg_try_advisory_lock(83418,:slot)"),
                                {"slot": victim_slot},
                            ).scalar_one()
                            quota.commit()
                            if not acquired:
                                continue
                            try:
                                size = candidate.stat().st_size
                                candidate.unlink()
                                occupied -= size
                            finally:
                                quota.execute(
                                    text("SELECT pg_advisory_unlock(83418,:slot)"),
                                    {"slot": victim_slot},
                                )
                                quota.commit()
                        if occupied + required > policy.max_bytes:
                            raise PlatformError("SOS-CACHE-CAPACITY")
                        storage.get(key, path, expected_sha256=dataset["version"])
                    finally:
                        quota.execute(text("SELECT pg_advisory_unlock(83419,0)"))
                        quota.commit()
            os.utime(path, None)
            yield path
        finally:
            lease.execute(text("SELECT pg_advisory_unlock(83418,:slot)"), {"slot": slot})
            lease.commit()
