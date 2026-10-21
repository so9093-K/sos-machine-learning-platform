"""PR #33의 일회성 속도/RSS/Spill 비교. 최종 PR에서 제거한다."""

import os
import resource
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import polars as pl

from sos_platform import ml_training
from sos_platform.ml_input import TRAINING_SELECTION_CANONICAL_V1, TRAINING_SELECTION_CURRENT
from sos_platform.schemas import MLTrainingInputPolicy


def generate(path: Path, count: int, columns: int) -> None:
    values = np.arange(count, dtype=np.float64)
    pl.DataFrame({
        f"f{i}": np.sin(values / (i + 17)) + i
        for i in range(columns)
    }).write_parquet(path)


def train(path: Path, columns: int, rows: int, version: str, memory_mib: int) -> None:
    @contextmanager
    def scan(_store, _dataset):
        yield pl.scan_parquet(path)

    ml_training.pinned_scan_dataset = scan
    started = time.monotonic()
    matrix, evidence = ml_training.training_matrix(
        None, {}, [f"f{i}" for i in range(columns)],
        input_policy=MLTrainingInputPolicy(mode="sample", rows=rows, seed=42),
        selection_algorithm_version=version,
        worker_memory_mib=memory_mib,
    )
    elapsed = time.monotonic() - started
    assert len(matrix) == rows
    assert evidence["method"] == version
    print(
        f"BENCH_RESULT version={version} rows={evidence['source_rows']} cols={columns} "
        f"sample={rows} seconds={elapsed:.2f} "
        f"process_max_rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}",
        flush=True,
    )


def current_rss_mib(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def watch(cmd: list[str], root: Path) -> None:
    root.mkdir()
    env = {**os.environ, "POLARS_TEMP_DIR": str(root)}
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    peak_rss = 0.0
    peak_stage = 0
    while process.poll() is None:
        peak_rss = max(peak_rss, current_rss_mib(process.pid))
        stage = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        peak_stage = max(peak_stage, stage)
        time.sleep(0.15)
    text, _ = process.communicate()
    print(text, end="", flush=True)
    print(f"BENCH_WATCH pid={process.pid} peak_rss_mib={peak_rss:.0f} peak_stage_mib={peak_stage / 2**20:.0f}")
    if process.returncode != 0:
        raise SystemExit(process.returncode)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[0] == "create":
        generate(Path(args[1]), int(args[2]), int(args[3]))
    elif args[0] == "train":
        train(Path(args[1]), int(args[2]), int(args[3]), args[4], int(args[5]))
    else:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            samples = [(1_370_000, 63, 100_000), (1_370_000, 8, 100_000)]
            for index, (count, cols, k) in enumerate(samples):
                data = root / f"input-{index}.parquet"
                subprocess.run(
                    [sys.executable, __file__, "create", str(data), str(count), str(cols)],
                    check=True,
                )
                for j, (version, budget) in enumerate((
                    (TRAINING_SELECTION_CANONICAL_V1, 3072),
                    (TRAINING_SELECTION_CURRENT, 2048),
                )):
                    watch([
                        sys.executable, __file__, "train", str(data),
                        str(cols), str(k), version, str(budget),
                    ], root / f"stage-{index}-{j}")
