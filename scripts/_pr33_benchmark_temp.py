"""PR #33 일회성 비교 전용. 결과 확인 후 작업 브랜치에서 제거한다."""

import argparse
import contextlib
import resource
import time
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from sos_platform import ml_training
from sos_platform.ml_input import TRAINING_SELECTION_CANONICAL_V1, TRAINING_SELECTION_CURRENT
from sos_platform.schemas import MLTrainingInputPolicy


def generate(path: Path, rows: int, features: int) -> None:
    rng = np.random.default_rng(101)
    columns = [f"f{i}" for i in range(features)]
    with pq.ParquetWriter(path, pa.schema([(name, pa.float64()) for name in columns])) as writer:
        for offset in range(0, rows, 32768):
            size = min(32768, rows - offset)
            values = rng.random((size, features), dtype=np.float64)
            writer.write_table(pa.table({
                name: pa.array(values[:, i]) for i, name in enumerate(columns)
            }))
    print(f"GENERATED rows={rows} features={features} bytes={path.stat().st_size}", flush=True)


def select(path: Path, rows: int, features: int, version: str) -> None:
    algorithm = {
        "v1": TRAINING_SELECTION_CANONICAL_V1,
        "v2": TRAINING_SELECTION_CURRENT,
    }.get(version)
    ml_training.pinned_scan_dataset = lambda *_args: contextlib.nullcontext(
        pl.scan_parquet(path)
    )
    policy = MLTrainingInputPolicy.model_validate({"mode": "sample", "rows": 100000, "seed": 11})
    start = time.perf_counter()
    if version == "v2_pos":
        from sos_platform.ml_sampling import streaming_canonical_sample

        selected, evidence = streaming_canonical_sample(
            pinned_scan_dataset=ml_training.pinned_scan_dataset,
            store=None, dataset={"version": "benchmark"},
            features=[f"f{i}" for i in range(features)],
            requested_rows=100000, seed=11, budget_mib=4096,
            selection_strategy="positions",
        )
    else:
        selected, evidence = ml_training.training_matrix(
            None, {"version": "benchmark"}, [f"f{i}" for i in range(features)],
            input_policy=policy, selection_algorithm_version=algorithm, worker_memory_mib=4096
        )
    elapsed = time.perf_counter() - start
    rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(
        f"RESULT rows={rows} features={features} version={version} "
        f"elapsed_s={elapsed:.3f} peak_rss_mib={rss_mib:.1f} "
        f"selected={len(selected)} sha256={evidence['selection_sha256']}",
        flush=True,
    )



def duckdb_v1(path: Path, rows: int, features: int, *, arrow_input: bool = False) -> None:
    """DuckDB 외부 정렬과 기존 v1 우선순위 선택 결과가 동일한지 비교."""
    import hashlib
    import duckdb

    columns = [f"f{i}" for i in range(features)]
    projection = ", ".join(f'"{col}"' for col in columns)
    scratch = path.parent / "duckdb-spill"
    scratch.mkdir(exist_ok=True)
    start = time.perf_counter()
    conn = duckdb.connect()
    conn.execute("SET memory_limit = '512MB'")
    conn.execute(f"SET temp_directory = '{scratch}'")
    if arrow_input:
        # 기존 pinned_scan_dataset(LazyFrame)의 Reader 계약을 모방한다.
        source = pl.scan_parquet(path).select(columns)
        arrow_schema = source.head(0).collect().to_arrow().schema

        def batches():
            for frame in source.collect_batches(chunk_size=8192, maintain_order=True):
                yield from frame.to_arrow().to_batches(max_chunksize=8192)

        conn.register("sos_source", pa.RecordBatchReader.from_batches(arrow_schema, batches()))
        reader = conn.execute(
            f"SELECT {projection} FROM sos_source ORDER BY {projection}"
        ).fetch_record_batch(rows_per_batch=8192)
    else:
        reader = conn.execute(
            f"SELECT {projection} FROM read_parquet(?) ORDER BY {projection}", [str(path)]
        ).fetch_record_batch(rows_per_batch=8192)
    sample_size = 100000
    selected = np.empty((sample_size, features), dtype=np.float64)
    selected_priority = np.empty(sample_size, dtype=np.uint64)
    selected_rows = 0
    rng = np.random.default_rng(11)
    observed = 0
    for batch in reader:
        frame = pl.from_arrow(batch)
        matrix = np.asarray(frame.to_numpy(), dtype=np.float64)
        observed += frame.height
        priorities = rng.integers(
            0, np.iinfo(np.uint64).max, size=frame.height, dtype=np.uint64
        )
        incoming = min(frame.height, sample_size - selected_rows)
        if incoming:
            stop = selected_rows + incoming
            selected[selected_rows:stop] = matrix[:incoming]
            selected_priority[selected_rows:stop] = priorities[:incoming]
            selected_rows = stop
        if selected_rows < sample_size or incoming == frame.height:
            continue
        matrix = matrix[incoming:]
        priorities = priorities[incoming:]
        candidates = np.flatnonzero(priorities < selected_priority[:selected_rows].max())
        if not candidates.size:
            continue
        combined = np.concatenate((
            selected_priority[:selected_rows], priorities[candidates]
        ))
        chosen = np.argpartition(combined, sample_size - 1)[:sample_size]
        keep = np.zeros(sample_size, dtype=bool)
        keep[chosen[chosen < sample_size]] = True
        dropped = np.flatnonzero(~keep)
        replacement = candidates[chosen[chosen >= sample_size] - sample_size]
        selected[dropped] = matrix[replacement]
        selected_priority[dropped] = priorities[replacement]
    assert observed == rows
    selected = np.ascontiguousarray(selected[np.lexsort(selected.T[::-1])])
    digest = hashlib.sha256(selected.tobytes()).hexdigest()
    staged = sum(p.stat().st_size for p in scratch.rglob("*") if p.is_file())
    conn.close()
    elapsed = time.perf_counter() - start
    rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(
        f"RESULT rows={rows} features={features} version={'v1_arrow_duckdb' if arrow_input else 'v1_duckdb'} "
        f"elapsed_s={elapsed:.3f} peak_rss_mib={rss_mib:.1f} "
        f"stage_bytes_at_end={staged} selected={len(selected)} sha256={digest}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["generate", "v1", "v2", "v2_pos", "v1_duckdb", "v1_arrow_duckdb"])
    parser.add_argument("rows", type=int)
    parser.add_argument("features", type=int)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.path, args.rows, args.features)
    elif args.action in ("v1_duckdb", "v1_arrow_duckdb"):
        duckdb_v1(args.path, args.rows, args.features, arrow_input=args.action == "v1_arrow_duckdb")
    else:
        select(args.path, args.rows, args.features, args.action)


if __name__ == "__main__":
    main()
