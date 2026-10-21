"""PR #33 일회성 실서비스 저장소 연동 스모크. 최종 변경에서 제거한다."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import numpy as np
import polars as pl

from sos_platform.datasets import save_upload_file, iceberg_catalog
from sos_platform.ml_input import TRAINING_SELECTION_CURRENT
from sos_platform.object_store import ObjectStore
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
        finally:
            store.release_worker()
            store.engine.dispose()


if __name__ == "__main__":
    main()
