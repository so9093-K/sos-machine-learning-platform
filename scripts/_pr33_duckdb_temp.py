"""PR #33 일회성 실측: 전체 스캔 선택 결과·Peak RSS·Peak Stage를 비교한다."""

import argparse
import contextlib
import os
import resource
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from sos_platform import ml_training
from sos_platform.schemas import MLTrainingInputPolicy

EXPECTED = {
    (1370000, 8): "9922ec61f698de6e2cdaa30782d572afa96d985d23ceb84a547b75aa430452c4",
    (1370000, 63): "e5e28491bab93ca8a077d1f1de5111bfccc65010375c75c9bd66de1702c54ad3",
    (10000000, 8): "3c86b374d51aa9db405eee13915b6514c7c00a873e25f3de1abc00bf47d7e552",
}


def generate(path: Path, rows: int, features: int) -> None:
    rng = np.random.default_rng(101)
    names = [f"f{i}" for i in range(features)]
    with pq.ParquetWriter(path, pa.schema([(name, pa.float64()) for name in names])) as writer:
        for offset in range(0, rows, 32768):
            size = min(32768, rows - offset)
            matrix = rng.random((size, features), dtype=np.float64)
            writer.write_table(pa.table({
                name: pa.array(matrix[:, i]) for i, name in enumerate(names)
            }))
    print(f"GENERATED rows={rows} features={features} bytes={path.stat().st_size}", flush=True)


def measure(path: Path, rows: int, features: int) -> None:
    names = [f"f{i}" for i in range(features)]
    policy = MLTrainingInputPolicy.model_validate({"mode":"sample","rows":100000,"seed":11})
    ml_training.pinned_scan_dataset = lambda *_args: contextlib.nullcontext(pl.scan_parquet(path))
    with TemporaryDirectory(prefix="pr33-stage-") as stage:
        os.environ["POLARS_TEMP_DIR"] = stage
        stop = threading.Event()
        peaks = {"rss": 0, "disk": 0}

        def monitor():
            while not stop.is_set():
                try:
                    for line in Path("/proc/self/status").read_text().splitlines():
                        if line.startswith("VmRSS:"):
                            peaks["rss"] = max(peaks["rss"], int(line.split()[1]) * 1024)
                            break
                    used = 0
                    for file in Path(stage).rglob("*"):
                        if file.is_file():
                            try:
                                used += file.stat().st_size
                            except FileNotFoundError:
                                pass
                    peaks["disk"] = max(peaks["disk"], used)
                except OSError:
                    pass
                stop.wait(0.02)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        start = time.perf_counter()
        try:
            selected, evidence = ml_training.training_matrix(
                None, {"version": "benchmark"}, names, input_policy=policy,
                worker_memory_mib=2048,
            )
        finally:
            stop.set()
            thread.join()
        elapsed = time.perf_counter() - start
        actual = evidence["selection_sha256"]
        assert actual == EXPECTED[(rows,features)], (actual, EXPECTED[(rows,features)])
        assert selected.shape == (100000, features)
        print(
            f"RESULT rows={rows} features={features} elapsed_s={elapsed:.3f} "
            f"peak_rss_mib={peaks['rss'] / (1024**2):.1f} "
            f"peak_stage_mib={peaks['disk'] / (1024**2):.1f} "
            f"ru_maxrss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.1f} "
            f"selection_sha256={actual} evidence_method={evidence['method']}",
            flush=True,
        )
        assert peaks["rss"] < 2048 * 1024 * 1024, "RSS exceeds default Worker budget"
        assert peaks["disk"] <= 2048 * 1024 * 1024, "Stage exceeds default Worker budget"
        assert list(Path(stage).iterdir()) == [], "Attempt spill was not cleaned"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action", choices=["generate", "measure"])
    parser.add_argument("rows", type=int)
    parser.add_argument("features", type=int)
    parser.add_argument("path", type=Path)
    args=parser.parse_args()
    if args.action=="generate":
        generate(args.path,args.rows,args.features)
    else:
        measure(args.path,args.rows,args.features)


if __name__ == "__main__":
    main()
