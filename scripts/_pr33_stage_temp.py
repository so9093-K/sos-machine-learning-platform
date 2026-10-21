"""일회성: Polars Streaming Sink → DuckDB 직접 Parquet 외부 정렬 비용 측정."""

import contextlib
import hashlib
import os
import resource
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import numpy as np
import polars as pl


def main() -> None:
    path = Path("/tmp/pr33/input.parquet")
    features = [f"f{i}" for i in range(63)]
    columns = ", ".join(f'"{v}"' for v in features)
    with TemporaryDirectory(prefix="pr33-staged-") as directory:
        root = Path(directory)
        stop = threading.Event()
        peak = {"stage": 0, "rss": 0}

        def track():
            while not stop.is_set():
                try:
                    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                    peak["stage"] = max(total, peak["stage"])
                    peak["rss"] = max(peak["rss"], resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
                except OSError:
                    pass
                stop.wait(0.02)

        task = threading.Thread(target=track, daemon=True)
        task.start()
        start = time.perf_counter()
        staged = root / "source.parquet"
        pl.scan_parquet(path).select(features).sink_parquet(staged)
        conn = duckdb.connect(
            database=":memory:", config={
                "memory_limit":"512MiB", "threads":"1",
                "temp_directory":str(root/"spill"),
                "max_temp_directory_size":"1024MiB",
            }
        )
        try:
            rows = conn.execute(
                f"SELECT {columns} FROM read_parquet(?) ORDER BY {columns}", [str(staged)]
            ).fetch_record_batch(rows_per_batch=8192)
            sample_size = 100000
            selected = np.empty((sample_size, len(features)),dtype=np.float64)
            priorities = np.empty(sample_size,dtype=np.uint64)
            filled = 0
            observed = 0
            rng = np.random.default_rng(11)
            for batch in rows:
                matrix = np.asarray(pl.from_arrow(batch).to_numpy(),dtype=np.float64)
                observed += len(matrix)
                new_p = rng.integers(0,np.iinfo(np.uint64).max,size=len(matrix),dtype=np.uint64)
                first = min(len(matrix),sample_size-filled)
                if first:
                    selected[filled:filled+first] = matrix[:first]
                    priorities[filled:filled+first] = new_p[:first]
                    filled += first
                if filled < sample_size or first == len(matrix):
                    continue
                matrix = matrix[first:]
                new_p = new_p[first:]
                candidates = np.flatnonzero(new_p < priorities.max())
                if not len(candidates):
                    continue
                all_p = np.concatenate((priorities,new_p[candidates]))
                chosen = np.argpartition(all_p,sample_size-1)[:sample_size]
                retained = np.zeros(sample_size,dtype=bool)
                retained[chosen[chosen<sample_size]] = True
                missing = np.flatnonzero(~retained)
                replacements = candidates[chosen[chosen>=sample_size]-sample_size]
                selected[missing] = matrix[replacements]
                priorities[missing] = new_p[replacements]
            selected = np.ascontiguousarray(selected[np.lexsort(selected.T[::-1])])
            result = hashlib.sha256(selected.tobytes()).hexdigest()
        finally:
            conn.close()
            stop.set()
            task.join()
        print(
            f"STAGED result={result} elapsed_s={time.perf_counter()-start:.3f} "
            f"peak_stage_mib={peak['stage']/1024**2:.1f} "
            f"peak_rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f}",
            flush=True,
        )
        assert observed == 1370000
        assert result == "e5e28491bab93ca8a077d1f1de5111bfccc65010375c75c9bd66de1702c54ad3"


if __name__ == "__main__":
    main()
