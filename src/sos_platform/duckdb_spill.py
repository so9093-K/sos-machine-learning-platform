"""Attempt가 소유한 Stage 디렉터리로 Spill하는 DuckDB 연결.

외부 정렬·집계처럼 전체 데이터의 순서가 필요한 계산을 메모리 예산 안에서 디스크를 써서 실행한다.
데이터 크기에 따라 처리 경로를 나누지 않고, 메모리에 담을 수 없는 중간 결과만 Stage로 넘긴다.
Stage 공간이나 메모리로도 수용할 수 없을 때만 호출자가 정한 오류로 보고한다.
"""

import errno
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import duckdb
import pyarrow.parquet as pq

from .errors import PlatformError
from .execution_budget import ExecutionBudget

_MIB = 1024 * 1024


@dataclass(frozen=True)
class SpillBudget:
    """한 계산이 쓸 수 있는 Stage와 자원 한도.

    Attributes:
        stage: 계산을 소유한 Attempt(또는 생산 Run)의 임시 디렉터리.
        memory_mib: 프로세스 전체 RSS 예산. DuckDB에는 그중 일부만 준다.
        max_stage_bytes: Stage 디렉터리 전체에 허용하는 디스크 크기.
        stage_error: Stage 공간을 넘었을 때의 오류 코드.
        capacity_error: 메모리로 수용할 수 없을 때의 오류 코드.
    """

    stage: Path
    memory_mib: int
    max_stage_bytes: int
    stage_error: str
    capacity_error: str

    @classmethod
    def for_worker(cls, stage: Path, *, stage_error: str, capacity_error: str) -> "SpillBudget":
        """Worker 실행 예산(RSS·Stage)을 쓰는 Attempt 계산."""
        budget = ExecutionBudget.from_env()
        return cls(stage, budget.max_rss_mib, budget.max_stage_mib * _MIB, stage_error, capacity_error)


def quoted(name: str) -> str:
    """검증된 컬럼 이름을 DuckDB SQL 식별자로 안전하게 인용한다."""
    return '"' + name.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def stage_exhausted(exc: BaseException) -> bool:
    """디스크 공간·Spill 할당량 초과만 Stage 용량 오류로 분류한다."""
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return True
    message = str(exc).lower()
    return any(phrase in message for phrase in (
        "no space left on device",
        "disk quota exceeded",
        "os error 28",
        "maximum temporary directory size",
        "max_temp_directory_size",
        "temporary directory size limit",
    ))


def _stage_bytes(stage: Path) -> int:
    total = 0
    for root, _, files in os.walk(stage):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                continue
    return total


@contextmanager
def spill_connection(
    budget: SpillBudget, *, readable: Sequence[Path] = ()
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Stage 안에서만 읽고 쓰며 Spill하는 DuckDB 연결.

    Args:
        budget: Stage·메모리 한도와 초과 시 오류 코드.
        readable: Stage 밖에서 읽어야 하는 파일. 그 밖의 외부 접근은 막는다.

    Raises:
        PlatformError: Stage 공간(`stage_error`) 또는 메모리(`capacity_error`)로 수용할 수 없다.
    """
    if budget.memory_mib <= 0 or budget.stage.is_symlink() or not budget.stage.is_dir():
        raise ValueError("Spill stage is unavailable")
    remaining = budget.max_stage_bytes - _stage_bytes(budget.stage)
    if remaining < _MIB:
        raise PlatformError(budget.stage_error)
    # 작은 실행에서는 현재 특성을 보존하고, 큰 예산에서는 정렬 Buffer와 병렬성을 늘리되
    # 나머지 메모리는 Reader·모델에 남겨 둔다.
    duckdb_mib = max(128, min(1024, budget.memory_mib // 4))
    try:
        conn = duckdb.connect(database=":memory:", config={
            "memory_limit": f"{duckdb_mib}MiB",
            "threads": "2" if duckdb_mib >= 1024 else "1",
            "temp_directory": str(budget.stage / "spill"),
            "max_temp_directory_size": f"{remaining // _MIB}MiB",
            "allow_community_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "allow_unsigned_extensions": "false",
        })
    except (duckdb.OutOfMemoryException, duckdb.IOException) as exc:
        _raise_resource_error(budget, exc)
        raise
    try:
        if readable:
            conn.execute(f"SET allowed_paths = [{', '.join(literal(str(p)) for p in readable)}]")
        conn.execute(f"SET allowed_directories = [{literal(str(budget.stage))}]")
        conn.execute("SET enable_external_access = false")
        conn.execute("SET lock_configuration = true")
        yield conn
    except (duckdb.OutOfMemoryException, duckdb.IOException) as exc:
        _raise_resource_error(budget, exc)
        raise
    finally:
        conn.close()


def order_statistics(
    path: Path, column: str, ranks: Sequence[int], budget: SpillBudget,
) -> list:
    """오름차순 정렬했을 때 지정한 순위(0부터)의 값. 전체를 외부 정렬하고 필요한 Row Group만 읽는다.

    정렬은 Stage에 Spill하며 결과 파일은 계산 뒤 지운다. 결측(null)은 마지막 순위다.
    """
    readable = [] if path.resolve().is_relative_to(budget.stage.resolve()) else [path]
    ordered = budget.stage / f"ordered-{uuid4().hex}.parquet"
    found = {}
    try:
        with spill_connection(budget, readable=readable) as conn:
            conn.execute(
                f"COPY (SELECT {quoted(column)} FROM read_parquet({literal(str(path))}) "
                f"ORDER BY {quoted(column)}) TO {literal(str(ordered))} (FORMAT parquet)"
            )
        with pq.ParquetFile(ordered) as reader:
            offset = 0
            for group in range(reader.num_row_groups):
                length = reader.metadata.row_group(group).num_rows
                wanted = [rank for rank in ranks if offset <= rank < offset + length]
                if wanted:
                    values = reader.read_row_group(group, columns=[column]).column(0)
                    found.update((rank, values[rank - offset].as_py()) for rank in wanted)
                offset += length
    finally:
        ordered.unlink(missing_ok=True)
    if set(found) != set(ranks):
        raise ValueError("Order statistic rank is outside the data")
    return [found[rank] for rank in ranks]


def _raise_resource_error(budget: SpillBudget, exc: BaseException) -> None:
    """자원 한도 초과면 호출자의 오류 코드로 바꾼다. 그 밖의 오류는 호출자가 다시 던진다."""
    if stage_exhausted(exc):
        raise PlatformError(budget.stage_error) from exc
    if isinstance(exc, duckdb.OutOfMemoryException):
        raise PlatformError(budget.capacity_error) from exc
