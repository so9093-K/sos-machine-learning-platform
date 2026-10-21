"""PR #33 임시 실험: Worker 예산별 DuckDB 정렬 자원 배분 비교. 최종 Diff에서 제거."""

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

from sos_platform import ml_sampling, ml_training
from sos_platform.errors import PlatformError
from sos_platform.schemas import MLTrainingInputPolicy


def generate(path: Path, nrows: int, width: int) -> None:
    names = [f"f{i}" for i in range(width)]
    rng = np.random.default_rng(101)
    with pq.ParquetWriter(path, pa.schema([(name, pa.float64()) for name in names])) as writer:
        for offset in range(0, nrows, 32768):
            rows = min(32768, nrows - offset)
            matrix = rng.random((rows, width), dtype=np.float64)
            writer.write_table(pa.table({name: pa.array(matrix[:, i]) for i, name in enumerate(names)}))
    print(f"GENERATED rows={nrows} width={width} bytes={path.stat().st_size}", flush=True)


def run(path: Path, nrows: int, width: int, memory: int, variant: str) -> None:
    os.environ["SOS_WORKER_MAX_STAGE_MIB"] = "2048"
    names = [f"f{i}" for i in range(width)]
    ml_training.pinned_scan_dataset = lambda *_args: contextlib.nullcontext(pl.scan_parquet(path))
    expected = {
        (1370000, 8): "9922ec61f698de6e2cdaa30782d572afa96d985d23ceb84a547b75aa430452c4",
        (1370000, 63): "e5e28491bab93ca8a077d1f1de5111bfccc65010375c75c9bd66de1702c54ad3",
    }[(nrows, width)]
    real_connect = ml_sampling.duckdb.connect
    configs = []

    def connect(*args, **kwargs):
        config = dict(kwargs["config"])
        if variant == "baseline":
            # PR 이전 메모리 512MiB 상한과 단일 정렬 스레드 설정.
            config["memory_limit"] = f"{max(128, min(512, memory // 4))}MiB"
            config["threads"] = "1"
        elif variant == "scaled":
            config["memory_limit"] = f"{max(128, min(1024, memory // 4))}MiB"
            config["threads"] = "2" if memory >= 4096 else "1"
        elif variant == "parallel":
            config["memory_limit"] = f"{max(128, min(1024, memory // 4))}MiB"
            config["threads"] = "4" if memory >= 8192 else "2"
        configs.append((config["memory_limit"], config["threads"]))
        return real_connect(*args, **{**kwargs, "config": config})

    ml_sampling.duckdb.connect = connect
    with TemporaryDirectory(prefix="pr33-tune-") as work:
        stage = Path(work) / "stage"
        stage.mkdir()
        os.environ["POLARS_TEMP_DIR"] = str(stage)
        peak = {"disk": 0, "rss": 0}
        stop = threading.Event()

        def monitor():
            while not stop.wait(0.035):
                try:
                    size = 0
                    for f in stage.rglob("*"):
                        if f.is_file():
                            try:
                                size += f.stat().st_size
                            except FileNotFoundError:
                                pass
                    peak["disk"] = max(peak["disk"], size)
                    for line in Path("/proc/self/status").read_text().splitlines():
                        if line.startswith("VmRSS:"):
                            peak["rss"] = max(peak["rss"], int(line.split()[1]) * 1024)
                            break
                except OSError:
                    pass

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        t0 = time.perf_counter()
        status, sha = "success", ""
        try:
            matrix, evidence = ml_training.training_matrix(
                None, {"version": "benchmark"}, names,
                input_policy=MLTrainingInputPolicy(mode="sample", rows=100000, seed=11),
                worker_memory_mib=memory,
            )
            sha = evidence["selection_sha256"]
            if sha != expected or matrix.shape != (100000, width):
                raise AssertionError("Existing v1 selection differs")
        except PlatformError as exc:
            status = exc.code
        except Exception as exc:
            status = type(exc).__name__ + ":" + str(exc)[:120]
        finally:
            stop.set()
            thread.join()
        elapsed = time.perf_counter() - t0
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        remaining = len(list(stage.iterdir()))
        print(
            f"MEASURE rows={nrows} width={width} budget_mib={memory} "
            f"variant={variant} duckdb={configs} status={status} "
            f"time_s={elapsed:.3f} rss_mib={rss:.1f} "
            f"stage_peak_mib={peak['disk']/1024**2:.1f} "
            f"within_logical_rss={rss < memory} sha={sha} stage_left={remaining}",
            flush=True,
        )
        assert remaining == 0, "Temporary stage leaked"
        assert status == "success" or status == "SOS-ML-TRAIN-CAPACITY", status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["generate", "measure"])
    parser.add_argument("rows", type=int)
    parser.add_argument("features", type=int)
    parser.add_argument("path", type=Path)
    parser.add_argument("--memory", type=int, default=2048)
    parser.add_argument("--variant", choices=["baseline", "scaled", "parallel", "product"], default="baseline")
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.path, args.rows, args.features)
    else:
        run(args.path, args.rows, args.features, args.memory, args.variant)


if __name__ == "__main__":
    main()
