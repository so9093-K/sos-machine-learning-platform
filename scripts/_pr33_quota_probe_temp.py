"""PR #33 일회성 실제 DuckDB Spill 용량 오류 관찰 (제품에 포함하지 않음)."""

import contextlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from sos_platform import ml_training
from sos_platform.errors import PlatformError
from sos_platform.schemas import MLTrainingInputPolicy


def main():
    with TemporaryDirectory(prefix="pr33-real-quota-") as folder:
        directory = Path(folder)
        source = directory / "source.parquet"
        rng = np.random.default_rng(25)
        names = [f"f{n}" for n in range(63)]
        with pq.ParquetWriter(
            source, pa.schema([(n, pa.float64()) for n in names])
        ) as writer:
            for _ in range(30):
                block = rng.integers(0, 32, size=(32768, len(names))).astype(np.float64)
                writer.write_table(pa.table({
                    name: pa.array(block[:, i]) for i, name in enumerate(names)
                }))
        original = pl.scan_parquet(source)
        ml_training.pinned_scan_dataset = lambda *_args: contextlib.nullcontext(original)
        stage = directory / "stage"
        stage.mkdir()
        os.environ["POLARS_TEMP_DIR"] = str(stage)
        os.environ["SOS_WORKER_MAX_STAGE_MIB"] = "128"
        print(f"INPUT source_bytes={source.stat().st_size} stage_limit_mib=128", flush=True)
        try:
            matrix, evidence = ml_training.training_matrix(
                None, {"version":"test"}, names,
                input_policy=MLTrainingInputPolicy(mode="sample", rows=10000, seed=42),
                worker_memory_mib=2048,
            )
            print(f"OUTCOME success selected={len(matrix)} sha={evidence['selection_sha256']}", flush=True)
        except PlatformError as exc:
            print(f"OUTCOME platform_error={exc.code} cause={type(exc.__cause__).__name__} cause_message={str(exc.__cause__)[:500]}", flush=True)
            if exc.code == "SOS-ML-TRAIN-STAGE-LIMIT":
                print("STAGE_ERROR_OK", flush=True)
            elif exc.code == "SOS-ML-TRAIN-CAPACITY":
                print("MEMORY_ERROR_OBSERVED", flush=True)
            else:
                raise
        except Exception as exc:
            print(f"OUTCOME unhandled={type(exc).__name__} details={str(exc)[:500]}", flush=True)
            raise
        finally:
            contents = list(stage.iterdir())
            print(f"STAGE_FILES_AFTER={len(contents)}", flush=True)
            assert not contents, contents


if __name__ == "__main__":
    main()
