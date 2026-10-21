"""중복 관측을 보존하는 순서 독립적·디스크 집계형 ML 표본 선택.

행 값과 등장 횟수를 로컬 SQLite 임시 파일에서 집계한 뒤,
각 논리적 관측(value, ordinal)에 Seed 기반 우선순위를 부여한다.
전체 Feature 값을 메모리에서 정렬하지 않으며, 임시 파일은 Attempt가 소유한다.
"""

import hashlib
import heapq
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, ContextManager

import numpy as np
import polars as pl

from .errors import PlatformError
from .execution_budget import ExecutionBudget
from .ml_input import INTEGER_DTYPES, NUMERIC, TRAINING_SELECTION_CURRENT


def _priority(value: bytes, ordinal: int, seed_bytes: bytes) -> int:
    """같은 논리적 관측·Seed에 대해 실행 순서와 무관한 128-bit 우선순위."""
    digest = hashlib.blake2b(
        value + ordinal.to_bytes(8, "little") + seed_bytes,
        digest_size=16,
        person=b"SOS-SAMPLE-V2",
    ).digest()
    return int.from_bytes(digest, "little")


def streaming_canonical_sample(
    *,
    pinned_scan_dataset: Callable[..., ContextManager[pl.LazyFrame]],
    store: object,
    dataset: dict,
    features: list[str],
    requested_rows: int,
    seed: int | None,
    budget_mib: int,
) -> tuple[np.ndarray, dict]:
    """Disk-backed Multiset 집계와 결정적 Bottom-K로 학습 행렬을 구성한다.

    Args:
        pinned_scan_dataset: Snapshot/파일 판본이 고정된 원본 스캔 Context Manager.
        store: Dataset 저장소.
        dataset: Dataset 참조.
        features: 모델에 전달할 숫자 Feature 컬럼의 순서.
        requested_rows: 변경하지 않을 표본 행 수(K).
        seed: 입력 선택 전용 32-bit Seed.
        budget_mib: 실행 Worker의 RSS 예산. 표본 저장량 계산에 사용한다.

    Returns:
        Feature 정렬된 float64 행렬과 소스·선택 근거.

    Raises:
        ValueError: 입력 Shape·dtype·값 또는 행 수가 계약에 맞지 않을 때.
        PlatformError: 명시적인 Worker 메모리·스테이징 예산을 초과할 때.

    Notes:
        서로 다른 원본 물리 행도 Feature 값이 동일하면 논리적으로 구별하지
        않는다. 중복 횟수는 정확히 보존한다. 해시 충돌은 논리적 관측을
        병합하지 않으며, BLAKE2b 출력은 오직 결정적 우선순위로 사용한다.
    """
    if seed is None:
        raise ValueError("Sample selection requires a seed")
    if not features or len(features) != len(set(features)):
        raise ValueError("Training features must be nonempty and distinct")
    if requested_rows < 2:
        raise ValueError("Sample rows must be at least two")

    # SQLite 커서와 Python Heap에 보존할 선택 키까지 함께 예산에 반영한다.
    per_selected_row = len(features) * 8 * 3 + 256
    selection_bytes = int(budget_mib * 1024 * 1024) // 8
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
    source_rows = 0
    seed_bytes = seed.to_bytes(4, "little")

    with TemporaryDirectory(prefix="canonical-sample-", dir=root) as staging:
        db_path = Path(staging) / "multiset.sqlite3"
        with sqlite3.connect(db_path) as db:
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-8192")
            db.execute(
                "CREATE TABLE observations (value BLOB PRIMARY KEY, occurrences INTEGER NOT NULL) WITHOUT ROWID"
            )
            statement = (
                "INSERT INTO observations(value, occurrences) VALUES (?, 1) "
                "ON CONFLICT(value) DO UPDATE SET occurrences=occurrences+1"
            )
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
                    matrix = np.asarray(frame.to_numpy(), dtype="<f8")
                    if not np.isfinite(matrix).all():
                        raise ValueError("Training features must be finite numeric values")
                    # +0.0/-0.0은 ML Feature 값과 정렬 의미가 같으므로 통일한다.
                    matrix[matrix == 0] = 0.0
                    matrix = np.ascontiguousarray(matrix)
                    with db:
                        db.executemany(statement, (row.tobytes(),) for row in matrix)
                    if db_path.stat().st_size > stage_limit:
                        raise PlatformError("SOS-ML-TRAIN-STAGE-LIMIT")

            if source_rows < 2:
                raise ValueError("Training requires at least two rows")
            if source_rows < requested_rows:
                raise ValueError("Sample rows exceed Dataset row count")

            # 저장소는 값의 BLOB 전체를 PRIMARY KEY로 비교한다. Digest 충돌이나
            # 순서에 의존해 동일 관측을 잃지 않는다.
            heap: list[tuple[int, bytes, int]] = []
            for key, count in db.execute("SELECT value, occurrences FROM observations"):
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
    order = np.lexsort(selected.T[::-1])
    selected = np.ascontiguousarray(selected[order], dtype=np.float64)
    selection = {
        "source_rows": source_rows,
        "selected_rows": len(selected),
        "method": TRAINING_SELECTION_CURRENT,
        "algorithm_version": TRAINING_SELECTION_CURRENT,
        "requested_rows": requested_rows,
        "seed": seed,
        "selection_sha256": hashlib.sha256(selected.tobytes()).hexdigest(),
    }
    return selected, selection
