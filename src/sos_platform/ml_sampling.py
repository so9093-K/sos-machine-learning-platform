"""기존 Canonical v1 표본 선택을 위한 제한된 메모리의 외부 정렬 Reader."""

import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import numpy as np
import polars as pl
import pyarrow as pa

from .errors import PlatformError
from .execution_budget import ExecutionBudget
from .ml_input import INTEGER_DTYPES


def canonical_sorted_batches(
    scan: pl.LazyFrame,
    features: list[str],
    schema: dict,
    *,
    chunk_rows: int,
    budget_mib: int,
) -> Iterator[pl.DataFrame]:
    """전체 입력을 검증·Canonical 정규화하고 기존 v1 순서로 외부 정렬한다.

    Args:
        scan: 판본·읽기 Lease가 호출자에 의해 고정된 LazyFrame.
        features: 사용자 지정 순서의 숫자형 Feature.
        schema: 원본 Feature dtype을 포함한 Polars 스키마.
        chunk_rows: Reader 및 결과의 최대 배치 행 수.
        budget_mib: 전체 Worker의 RSS 예산. DuckDB는 그 일부만 사용한다.

    Yields:
        원본 입력의 정렬 순서와 동등한 Float64 Canonical Feature 배치.

    Raises:
        ValueError: NULL·비유한값·정밀도 손실 가능 정수 또는 입력 오류.
        PlatformError: DuckDB 정렬이 작업 자원 예산을 수용하지 못할 때.

    Notes:
        고정된 Dataset Reader를 재사용하므로 파일 Lease·Iceberg Snapshot을
        우회하지 않는다. DuckDB의 설정은 프로세스 RSS를 강제하지 않으며
        물리 자원 제한은 기존 Worker의 감시·격리 책임이다.
    """
    if chunk_rows <= 0 or budget_mib <= 0:
        raise ValueError("Invalid training Reader budget")

    stage = os.getenv("POLARS_TEMP_DIR")
    root = Path(stage) if stage is not None else None
    if root is not None and (root.is_symlink() or not root.is_dir()):
        raise ValueError("Worker training stage directory is unavailable")

    arrow_schema = pa.schema([(name, pa.float64()) for name in features])

    def validated_batches() -> Iterator[pa.RecordBatch]:
        for frame in scan.collect_batches(chunk_size=chunk_rows, maintain_order=True):
            if any(frame.null_count().row(0)):
                raise ValueError("Training features contain null values")
            for name in features:
                if schema[name] in INTEGER_DTYPES:
                    values = frame[name]
                    if values.max() > 2**53 or values.min() < -(2**53):
                        raise ValueError("Integer feature exceeds the exact float64 range")
            matrix = np.array(frame.to_numpy(), dtype=np.float64, copy=True)
            if not np.isfinite(matrix).all():
                raise ValueError("Training features must be finite numeric values")
            # Float32/64의 -0을 +0으로 통일한 후 엔진 간 정렬 차이를 없앤다.
            matrix[matrix == 0] = 0.0
            yield pa.RecordBatch.from_arrays(
                [pa.array(matrix[:, index]) for index in range(len(features))],
                schema=arrow_schema,
            )

    stage_limit = ExecutionBudget.from_env().max_stage_mib
    if stage_limit <= 0:
        raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")
    # 작은 테스트 예산에서도 동작하되 보통 Worker에서는 전체 RSS의 일부만
    # DuckDB에 배정한다. 이 제한은 실제 RSS의 상한이 아니다.
    duckdb_mib = max(16, min(512, budget_mib // 4))
    quote = lambda name: '"' + name.replace('"', '""') + '"'
    columns = ", ".join(quote(name) for name in features)

    with TemporaryDirectory(prefix="canonical-sort-", dir=root) as directory:
        conn = duckdb.connect(
            database=":memory:",
            config={
                "memory_limit": f"{duckdb_mib}MiB",
                "threads": "1",
                "temp_directory": directory,
                "max_temp_directory_size": f"{stage_limit}MiB",
                "allow_community_extensions": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "allow_unsigned_extensions": "false",
            },
        )
        source_reader = pa.RecordBatchReader.from_batches(arrow_schema, validated_batches())
        result_reader = None
        try:
            conn.register("training_source", source_reader)
            conn.execute("SET enable_external_access = false")
            conn.execute("SET lock_configuration = true")
            # 정렬은 원본 전체에 적용되므로 선택에서 제외된 행도 검증된다.
            result_reader = conn.execute(
                f"SELECT {columns} FROM training_source ORDER BY {columns}"
            ).fetch_record_batch(rows_per_batch=chunk_rows)
            for batch in result_reader:
                yield pl.from_arrow(batch)
        except duckdb.OutOfMemoryException as exc:
            raise PlatformError("SOS-ML-TRAIN-CAPACITY") from exc
        finally:
            if result_reader is not None:
                result_reader.close()
            conn.close()
            source_reader.close()
