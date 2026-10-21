"""기존 Canonical v1의 입력 정렬을 Worker Stage의 DuckDB Spill로 실행한다."""

import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import polars as pl

from .errors import PlatformError
from .execution_budget import ExecutionBudget

_MIB = 1024 * 1024


def _quoted(name: str) -> str:
    """검증된 Feature 이름을 DuckDB SQL 식별자로 안전하게 인용한다."""
    return '"' + name.replace('"', '""') + '"'


def canonical_sorted_batches(
    scan: pl.LazyFrame,
    features: list[str],
    schema: dict,
    *,
    chunk_rows: int,
    budget_mib: int,
) -> Iterator[pl.DataFrame]:
    """고정된 Dataset의 전체 입력을 v1과 동일한 순서로 외부 정렬한다.

    Args:
        scan: 상위 pinned_scan_dataset의 Lease·Snapshot이 유지된 LazyFrame.
        features: 사용자 지정 순서의 숫자형 Feature 이름.
        schema: 원본 Feature dtype을 포함한 Polars 스키마.
        chunk_rows: 정렬 결과를 읽는 배치 크기.
        budget_mib: Worker 전체 RSS 예산. DuckDB에는 일부만 할당.

    Yields:
        Canonical 정렬된 Feature 배치. 검증과 Seed 기반 선택은 호출자가 수행한다.

    Raises:
        PlatformError: 임시 저장 또는 DuckDB 메모리 수용 한계.
        ValueError: 잘못된 Stage 디렉터리 및 실행 인자.

    Notes:
        원본을 재스캔하거나 파일 Lease를 우회하지 않는다. Streaming Sink와
        DuckDB Spill 모두 같은 Attempt 소유 Stage에 기록한다. DuckDB의
        메모리 설정만으로 Worker 전체 RSS의 상한을 보장하지 않는다.
    """
    if chunk_rows <= 0 or budget_mib <= 0:
        raise ValueError("Invalid training Reader budget")
    raw_root = os.getenv("POLARS_TEMP_DIR")
    root = Path(raw_root) if raw_root is not None else None
    if root is not None and (root.is_symlink() or not root.is_dir()):
        raise ValueError("Worker training stage directory is unavailable")

    limit_bytes = ExecutionBudget.from_env().max_stage_mib * _MIB
    if limit_bytes < _MIB:
        raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")

    # 기존 v1은 정렬 전에 Float32/64의 +0/-0을 동일 값으로 만든다.
    floats = [name for name in features if schema[name] in (pl.Float32, pl.Float64)]
    if floats:
        scan = scan.with_columns(
            pl.when(pl.col(name) == 0)
            .then(pl.lit(0.0, dtype=schema[name]))
            .otherwise(pl.col(name))
            .alias(name)
            for name in floats
        )

    columns = ", ".join(_quoted(name) for name in features)
    duckdb_mib = max(128, min(512, budget_mib // 4))
    with TemporaryDirectory(prefix="canonical-sort-", dir=root) as directory:
        stage = Path(directory)
        staged_file = stage / "input.parquet"
        # Polars의 Streaming Sink로 Reader의 전체 배열을 보유하지 않는다.
        scan.sink_parquet(staged_file)
        staged_size = staged_file.stat().st_size
        remaining_bytes = limit_bytes - staged_size
        if remaining_bytes < _MIB:
            raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")

        conn = duckdb.connect(
            database=":memory:",
            config={
                "memory_limit": f"{duckdb_mib}MiB",
                "threads": "1",
                "temp_directory": str(stage / "spill"),
                "max_temp_directory_size": f"{remaining_bytes // _MIB}MiB",
                "allow_community_extensions": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "allow_unsigned_extensions": "false",
            },
        )
        result_reader = None
        try:
            path_literal = "'" + str(staged_file).replace("'", "''") + "'"
            conn.execute(f"SET allowed_paths = [{path_literal}]")
            stage_literal = "'" + str(stage).replace("'", "''") + "'"
            conn.execute(f"SET allowed_directories = [{stage_literal}]")
            conn.execute("SET enable_external_access = false")
            conn.execute("SET lock_configuration = true")
            result_reader = conn.execute(
                f"SELECT {columns} FROM read_parquet(?) ORDER BY {columns}",
                [str(staged_file)],
            ).fetch_record_batch(rows_per_batch=chunk_rows)
            for batch in result_reader:
                yield pl.from_arrow(batch)
        except duckdb.OutOfMemoryException as exc:
            raise PlatformError("SOS-ML-TRAIN-CAPACITY") from exc
        finally:
            if result_reader is not None:
                result_reader.close()
            conn.close()
