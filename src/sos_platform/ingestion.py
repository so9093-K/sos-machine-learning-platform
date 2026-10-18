"""파일 수신의 자원 한도와 원본 바이트 무결성 검사."""

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from .errors import PlatformError, RuntimeConfigurationError

CHUNK_SIZE = 1024 * 1024
SUPPORTED_EXTENSIONS = (".csv", ".parquet", ".tsv", ".txt")
MULTIPART_OVERHEAD_BYTES = 64 * 1024


@dataclass(frozen=True)
class UploadPolicy:
    """서버가 적용하는 단일 파일 업로드 한도."""

    max_bytes: int

    @classmethod
    def from_env(cls) -> "UploadPolicy":
        raw = os.getenv("SOS_MAX_UPLOAD_MIB", "256")
        if not raw.isdecimal() or not 1 <= int(raw) <= 2048:
            raise RuntimeConfigurationError("SOS_MAX_UPLOAD_MIB must be between 1 and 2048")
        return cls(max_bytes=int(raw) * 1024 * 1024)

    def describe(self) -> dict:
        return {
            "max_size_bytes": self.max_bytes,
            "supported_extensions": list(SUPPORTED_EXTENSIONS),
        }


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    size_bytes: int
    sha256: str
    suffix: str


@contextmanager
def staged_upload(
    source: BinaryIO, name: str, cache: Path, *, max_bytes: int
) -> Iterator[StagedUpload]:
    """파일 전체를 메모리에 올리지 않고 제한된 청크로 임시 저장하고 해시를 계산한다."""
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise PlatformError("SOS-UPLOAD-FORMAT")
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with NamedTemporaryFile(dir=cache, suffix=suffix, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            while chunk := source.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise PlatformError("SOS-UPLOAD-TOO-LARGE")
                handle.write(chunk)
                digest.update(chunk)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if not size:
            raise PlatformError("SOS-UPLOAD-INVALID")
        yield StagedUpload(temporary, size, digest.hexdigest(), suffix)
    finally:
        temporary.unlink(missing_ok=True)
