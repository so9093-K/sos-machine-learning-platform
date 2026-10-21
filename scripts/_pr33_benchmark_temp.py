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
    }[version]
    ml_training.pinned_scan_dataset = lambda *_args: contextlib.nullcontext(
        pl.scan_parquet(path)
    )
    policy = MLTrainingInputPolicy.model_validate({"mode": "sample", "rows": 100000, "seed": 11})
    start = time.perf_counter()
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["generate", "v1", "v2"])
    parser.add_argument("rows", type=int)
    parser.add_argument("features", type=int)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.path, args.rows, args.features)
    else:
        select(args.path, args.rows, args.features, args.action)


if __name__ == "__main__":
    main()
