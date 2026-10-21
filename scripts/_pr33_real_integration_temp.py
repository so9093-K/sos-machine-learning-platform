"""PR #33 일회성 실서비스 저장소 연동 스모크. 최종 변경에서 제거한다."""

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from sos_platform.datasets import save_upload_file, iceberg_catalog
from sos_platform.ml_input import TRAINING_SELECTION_CURRENT
from sos_platform.object_store import ObjectStore
from sos_platform.ml_training_staging import training_stage_bytes, training_stage_path
from sos_platform.store import Store
from sos_platform.worker import run_once


def check(job: dict, expected: str) -> None:
    status = job["status"]
    print(
        f"REAL_INTEGRATION job_kind={job['kind']} status={status} "
        f"error_code={job.get('error_code')} attempts={len(job['attempts'])}",
        flush=True,
    )
    assert status == expected, json.dumps(job, default=str)[:4500]
    assert len(job["attempts"]) == 1
    assert job["attempts"][0]["status"] == expected



def exercise_interruption_and_capacity(store: Store, base: Path) -> None:
    """하나의 실제 Iceberg Snapshot에서 취소와 Stage 초과를 별도 Attempt로 확인."""
    file = base / "wide.parquet"
    width, rows = 48, 390_000
    columns = [f"f{i}" for i in range(width)]
    rng = np.random.default_rng(801)
    arrow_schema = pa.schema([(name, pa.float64()) for name in columns])
    with pq.ParquetWriter(file, arrow_schema) as writer:
        for offset in range(0, rows, 16384):
            count = min(16384, rows - offset)
            matrix = rng.random((count, width), dtype=np.float64)
            writer.write_table(pa.table({
                name: pa.array(matrix[:, i]) for i, name in enumerate(columns)
            }))
    print(f"REAL_INTEGRATION pressure_dataset_bytes={file.stat().st_size}", flush=True)
    with file.open("rb") as source:
        original = save_upload_file(store, "pr33-wide.parquet", source)
    table_name = "pr33_wide_" + uuid4().hex[:12]
    owner = "pr33-ci-actor"
    staged = store.create_job(
        kind="iceberg_import",
        dataset_id=original["id"],
        spec={"table_name": table_name},
        owner_actor=owner,
        reserve_iceberg_table=table_name,
    )
    assert run_once(store)
    imported = store.get_job(staged["id"])
    check(imported, "Succeeded")
    pinned = store.get_dataset(imported["result"]["dataset_id"])
    spec = {
        "algorithm": "isolation_forest",
        "dataset_version": pinned["version"],
        "features": columns,
        "params": {"n_estimators": 24, "random_state": 42},
        "training_input": {"mode": "sample", "rows": 100000, "seed": 117},
        "selection_algorithm_version": TRAINING_SELECTION_CURRENT,
    }

    os.environ["SOS_WORKER_MAX_STAGE_MIB"] = "2048"
    job = store.create_job(kind="ml_train", dataset_id=pinned["id"], spec=spec)
    cancel_status = {}
    def cancel_when_sorting():
        controller = Store(store.home)
        try:
            deadline = time.monotonic() + 80
            while time.monotonic() < deadline:
                current = controller.get_job(job["id"])
                if current["status"] in ("Succeeded", "Cancelled", "Failed"):
                    cancel_status["failure"] = f"Finished before staged cancel: {current['status']}"
                    return
                if current["status"] == "Running" and current["attempts"]:
                    attempt_id = current["attempts"][-1]["id"]
                    if training_stage_bytes(store.home, attempt_id) >= 2 * 1024 * 1024:
                        before = controller.request_cancel(job["id"])
                        cancel_status["requested"] = before["cancel_requested"]
                        cancel_status["attempt_id"] = attempt_id
                        return
                time.sleep(0.025)
            cancel_status["failure"] = "Training did not produce Stage data in 80 seconds"
        finally:
            controller.engine.dispose()
    watcher = threading.Thread(target=cancel_when_sorting, daemon=True)
    watcher.start()
    assert run_once(store)
    watcher.join(timeout=10)
    assert not watcher.is_alive(), "Cancellation watcher stuck"
    assert cancel_status.get("requested"), cancel_status
    cancelled = store.get_job(job["id"])
    check(cancelled, "Cancelled")
    assert cancelled["result"] is None
    attempt_id = cancelled["attempts"][-1]["id"]
    assert not training_stage_path(store.home, attempt_id).exists()
    storage = ObjectStore()
    assert storage.client.list_objects_v2(
        Bucket=storage.config.s3_bucket, Prefix=f"results/{job['id']}/"
    ).get("KeyCount", 0) == 0
    print(f"REAL_INTEGRATION CANCELLED_DURING_SORT attempt={attempt_id}", flush=True)

    os.environ["SOS_WORKER_MAX_STAGE_MIB"] = "128"
    constrained = store.create_job(kind="ml_train", dataset_id=pinned["id"], spec=spec)
    assert run_once(store)
    failed = store.get_job(constrained["id"])
    check(failed, "Failed")
    assert failed["error_code"] == "SOS-ML-TRAIN-STAGE-LIMIT", failed
    assert not training_stage_path(store.home, failed["attempts"][-1]["id"]).exists()
    assert storage.client.list_objects_v2(
        Bucket=storage.config.s3_bucket, Prefix=f"results/{constrained['id']}/"
    ).get("KeyCount", 0) == 0
    print(
        f"REAL_INTEGRATION STAGE_LIMIT_FAILED code={failed['error_code']} "
        f"attempt={failed['attempts'][-1]['id']}",
        flush=True,
    )

def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pr33-e2e-") as tmp:
        home = Path(tmp) / "home"
        store = Store(home)
        store.initialize()
        ObjectStore().ensure_bucket()
        assert store.acquire_worker()
        try:
            store.heartbeat()
            # 테스트에만 쓰는 데이터·새 테이블명. 외부 환경의 기존 데이터를 건드리지 않음.
            data = Path(tmp) / "input.parquet"
            n = 4000
            pl.DataFrame({
                "signal": (np.arange(n, dtype=np.float64) % 93) / 4,
                "context": (np.arange(n, dtype=np.float64) % 29) / 3,
            }).write_parquet(data)
            with data.open("rb") as src:
                dataset = save_upload_file(store, "pr33-input.parquet", src)
            assert dataset["kind"] == "file"
            table_name = "pr33_e2e_" + uuid4().hex[:12]
            owner = "pr33-ci-actor"
            import_job = store.create_job(
                kind="iceberg_import",
                dataset_id=dataset["id"],
                spec={"table_name": table_name},
                owner_actor=owner,
                reserve_iceberg_table=table_name,
            )
            assert run_once(store)
            imported = store.get_job(import_job["id"])
            check(imported, "Succeeded")
            iceberg_id = imported["result"]["dataset_id"]
            pinned = store.get_dataset(iceberg_id)
            assert pinned["kind"] == "iceberg"
            snapshot_id = pinned["version"]
            table = iceberg_catalog(store).load_table(pinned["location"])
            assert any(str(s.snapshot_id) == snapshot_id for s in table.snapshots())
            print(f"REAL_INTEGRATION iceberg_snapshot_verified={snapshot_id}", flush=True)

            spec = {
                "algorithm": "isolation_forest",
                "dataset_version": pinned["version"],
                "features": ["signal", "context"],
                "params": {"n_estimators": 12, "random_state": 42},
                "training_input": {"mode": "sample", "rows": 900, "seed": 17},
                "selection_algorithm_version": TRAINING_SELECTION_CURRENT,
            }
            job = store.create_job(kind="ml_train", dataset_id=pinned["id"], spec=spec)
            assert run_once(store)
            completed = store.get_job(job["id"])
            check(completed, "Succeeded")
            proof = completed["result"]["training_selection"]
            assert proof["source_rows"] == n
            assert proof["selected_rows"] == 900
            assert proof["method"] == TRAINING_SELECTION_CURRENT
            assert len(proof["selection_sha256"]) == 64
            revision_id = completed["result"]["model_revision_id"]
            revision = store.get_model_revision(revision_id)
            assert revision["dataset_id"] == pinned["id"]
            assert revision["dataset_version"] == snapshot_id
            assert revision["job_id"] == job["id"]
            storage = ObjectStore()
            response = storage.client.get_object(
                Bucket=storage.config.s3_bucket, Key=storage.key(revision["location"])
            )
            model_data = response["Body"].read()
            assert hashlib.sha256(model_data).hexdigest() == revision["sha256"]
            assert len(model_data) > 1000
            print(
                f"REAL_INTEGRATION SUCCESS snapshot={snapshot_id} "
                f"model_revision={revision_id} artifact_bytes={len(model_data)} "
                f"sha256={proof['selection_sha256']}",
                flush=True,
            )
            exercise_interruption_and_capacity(store, Path(tmp))
        finally:
            store.release_worker()
            store.engine.dispose()


if __name__ == "__main__":
    main()
