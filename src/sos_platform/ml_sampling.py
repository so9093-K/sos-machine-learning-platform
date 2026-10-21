"""전체 Feature 전역 정렬 없는 순서 독립적 Training Multiset Sampling.

입력은 작은 배치에서 중복 개수를 합친 뒤 해시 기반 디스크 파티션으로
분할한다. 일반 파티션은 제한된 메모리에서 집계하고, 큰 파티션만
로컬 SQLite로 집계한다. 논리적 관측의 해시 우선순위는 읽기 순서와 독립적이다.
"""

import hashlib
import heapq
import os
import sqlite3
import zlib
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import polars as pl

from .errors import PlatformError
from .execution_budget import ExecutionBudget
from .ml_input import INTEGER_DTYPES, NUMERIC, TRAINING_SELECTION_CURRENT

_BUCKETS = 256
_PARTITION_MEMORY_BYTES = 32 * 1024 * 1024


def _priority(value: bytes, ordinal: int, seed_bytes: bytes) -> int:
    """같은 논리적 관측·Seed에 대해 읽기 순서 독립적인 128-bit 우선순위."""
    digest = hashlib.blake2b(
        value + ordinal.to_bytes(8, "little") + seed_bytes,
        digest_size=16,
        person=b"SOS-SAMPLE-V2",
    ).digest()
    return int.from_bytes(digest, "little")


def _partition_records(path: Path, size: int) -> Iterator[tuple[bytes, int]]:
    """고정 폭 Feature 값·등장 횟수를 제한된 I/O 버퍼로 읽는다."""
    record_size = size + 8
    with path.open("rb") as handle:
        while block := handle.read(record_size * 8192):
            if len(block) % record_size:
                raise ValueError("Incomplete sampling stage record")
            for position in range(0, len(block), record_size):
                record = block[position : position + record_size]
                yield record[:size], int.from_bytes(record[size:], "little")


def _groups(
    path: Path, value_width: int, memory_limit: int, stage_limit: int
) -> Iterator[tuple[bytes, int]]:
    """작은 파티션은 메모리, 큰 파티션은 디스크에서 값별 등장 횟수를 집계."""
    if path.stat().st_size <= memory_limit:
        counts: dict[bytes, int] = {}
        for key, count in _partition_records(path, value_width):
            counts[key] = counts.get(key, 0) + count
        yield from counts.items()
        return

    database = path.with_suffix(".sqlite3")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA cache_size=-8192")
        connection.execute(
            "CREATE TABLE counts (value BLOB PRIMARY KEY, occurrences INTEGER NOT NULL) WITHOUT ROWID"
        )
        statement = (
            "INSERT INTO counts(value, occurrences) VALUES (?, ?) "
            "ON CONFLICT(value) DO UPDATE SET occurrences=occurrences+excluded.occurrences"
        )
        with connection:
            chunk: list[tuple[bytes, int]] = []
            for record in _partition_records(path, value_width):
                chunk.append(record)
                if len(chunk) == 8192:
                    connection.executemany(statement, chunk)
                    chunk.clear()
                    if database.stat().st_size > stage_limit:
                        raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")
            if chunk:
                connection.executemany(statement, chunk)
        if path.stat().st_size + database.stat().st_size > stage_limit:
            raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")
        yield from connection.execute("SELECT value, occurrences FROM counts")
    # 파티션별 디스크 집계가 끝나면 다음 파티션 이전에 여유 공간 회수.
    database.unlink(missing_ok=True)


def streaming_canonical_sample(
    *,
    pinned_scan_dataset: Callable[..., AbstractContextManager[pl.LazyFrame]],
    store: object,
    dataset: dict,
    features: list[str],
    requested_rows: int,
    seed: int | None,
    budget_mib: int,
) -> tuple[np.ndarray, dict]:
    """디스크 파티셔닝과 결정적 Bottom-K로 숫자형 학습 입력을 선택한다.

    Args:
        pinned_scan_dataset: Dataset Revision이 고정된 원본 스캔 Context Manager.
        store: Dataset 저장소.
        dataset: Dataset 참조.
        features: 학습 Feature 순서.
        requested_rows: 변경하지 않는 표본 행 수(K).
        seed: 입력 선택 전용 난수 Seed.
        budget_mib: Worker RSS 예산. 표본 메모리 수용 판단에 사용.

    Returns:
        Feature 순서대로 정렬한 float64 학습 행렬과 실제 입력 근거.

    Raises:
        ValueError: Dataset·Feature·행 수·값 검증 오류.
        PlatformError: 메모리·디스크 실행 예산 초과.

    Notes:
        중복 Feature 값의 관측은 모두 구별된 논리적 발생으로 간주한다.
        선택 해시의 충돌로 값이나 등장 횟수를 합치지 않는다. 원본
        물리적 행 식별·재현을 보장하는 계약은 아니다.
    """
    if seed is None:
        raise ValueError("Sample selection requires a seed")
    if not features or len(set(features)) != len(features):
        raise ValueError("Training features must be nonempty and distinct")
    if requested_rows < 2:
        raise ValueError("Sample rows must be at least two")

    # Heap: 키 바이트·우선순위·Python 오버헤드·최종 행렬을 모두 고려.
    per_selected_row = len(features) * 8 * 3 + 256
    selection_bytes = int(budget_mib * 1024 * 1024) // 3
    if requested_rows > selection_bytes // per_selected_row:
        raise PlatformError("SOS-ML-TRAIN-CAPACITY")

    stage_limit = ExecutionBudget.from_env().max_stage_mib * 1024 * 1024
    stage_root = os.getenv("POLARS_TEMP_DIR")
    if stage_root is not None:
        root = Path(stage_root)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Worker training stage directory is unavailable")
    else:
        root = None

    chunk_rows = max(1, min(8192, selection_bytes // max(1, len(features) * 8 * 4)))
    partition_limit = min(
        _PARTITION_MEMORY_BYTES, max(128 * 1024, budget_mib * 1024 * 1024 // 32)
    )
    value_width = len(features) * 8
    source_rows = 0
    staged_bytes = 0
    seed_bytes = seed.to_bytes(4, "little")

    with TemporaryDirectory(prefix="canonical-sample-", dir=root) as stage_dir:
        stage = Path(stage_dir)
        with ExitStack() as writers:
            buckets: dict[int, object] = {}
            with pinned_scan_dataset(store, dataset) as source:
                schema = source.collect_schema()
                if any(name not in schema for name in features):
                    raise ValueError("Unknown training feature")
                if any(schema[name] not in NUMERIC for name in features):
                    raise ValueError("Only numeric training features are supported")
                for frame in source.select(features).collect_batches(
                    chunk_size=chunk_rows, maintain_order=True
                ):
                    source_rows += frame.height
                    if any(frame.null_count().row(0)):
                        raise ValueError("Training features contain null values")
                    for name in features:
                        if schema[name] in INTEGER_DTYPES:
                            column = frame[name]
                            if column.max() > 2**53 or column.min() < -(2**53):
                                raise ValueError("Integer feature exceeds the exact float64 range")
                    matrix = np.array(frame.to_numpy(), dtype="<f8", copy=True)
                    if not np.isfinite(matrix).all():
                        raise ValueError("Training features must be finite numeric values")
                    matrix[matrix == 0] = 0.0
                    matrix = np.ascontiguousarray(matrix)
                    # 각 배치에서 중복을 먼저 합치므로 동일 값이 수백만 번 나오는
                    # 경우에도 같은 값을 불필요하게 모두 디스크에 쓰지 않는다.
                    counts = Counter(row.tobytes() for row in matrix)
                    for key, count in counts.items():
                        bucket = zlib.crc32(key) % _BUCKETS
                        if bucket not in buckets:
                            buckets[bucket] = writers.enter_context(
                                (stage / f"bucket-{bucket:03d}.bin").open("ab")
                            )
                        buckets[bucket].write(key + count.to_bytes(8, "little"))
                        staged_bytes += value_width + 8
                    if staged_bytes > stage_limit:
                        raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")

        if source_rows < 2:
            raise ValueError("Training requires at least two rows")
        if source_rows < requested_rows:
            raise ValueError("Sample rows exceed Dataset row count")

        heap: list[tuple[int, bytes, int]] = []
        for partition in sorted(stage.glob("bucket-*.bin")):
            for key, count in _groups(
                partition, value_width, partition_limit, stage_limit - staged_bytes
            ):
                for ordinal in range(count):
                    priority = _priority(key, ordinal, seed_bytes)
                    candidate = (-priority, key, ordinal)
                    if len(heap) < requested_rows:
                        heapq.heappush(heap, candidate)
                    elif candidate > heap[0]:
                        heapq.heapreplace(heap, candidate)

    selected = np.empty((requested_rows, len(features)), dtype="<f8")
    for index, (_, key, _) in enumerate(heap):
        selected[index] = np.frombuffer(key, dtype="<f8")
    selected = np.ascontiguousarray(selected[np.lexsort(selected.T[::-1])], dtype=np.float64)
    evidence = {
        "source_rows": source_rows,
        "selected_rows": len(selected),
        "method": TRAINING_SELECTION_CURRENT,
        "algorithm_version": TRAINING_SELECTION_CURRENT,
        "requested_rows": requested_rows,
        "seed": seed,
        "selection_sha256": hashlib.sha256(selected.tobytes()).hexdigest(),
    }
    return selected, evidence
