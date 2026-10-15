"""EDA 및 변환 작업을 별도 프로세스로 격리한다.

취소 요청 시 계산 종료를 기다리지 않고 자식 프로세스를 중단한다.
종료가 확인된 뒤에만 취소를 확정한다.
"""

import argparse
import json
import logging
import multiprocessing
import os
import signal
import time
from pathlib import Path

from .datasets import import_to_iceberg, scan_dataset
from .object_store import ObjectStore
from .processing import profile, transform
from .schemas import Transform
from .store import Store


def _perform(home: Path, database_url: str, job: dict, completed: Path, pending: Path) -> None:
    """계산은 자식 프로세스에서 수행하고 결과 확정은 부모가 담당한다."""
    try:
        store = Store(home, database_url=database_url)
        dataset = store.get_dataset(job["dataset_id"])
        if dataset is None:
            raise ValueError("Input dataset is missing")
        lf = scan_dataset(store, dataset)
        if job["kind"] == "profile":
            result = profile(lf, sample=None)
        elif job["kind"] == "transform":
            spec = Transform.model_validate(job["spec"]["transform"])
            lf = transform(lf, spec)
            lf.sink_parquet(pending)
            result = {"input_dataset_id": dataset["id"]}
        elif job["kind"] == "iceberg_import":
            result = {
                "iceberg_publish": import_to_iceberg(
                    store, dataset, job["spec"]["table_name"], job["attempts"][-1]["id"]
                )
            }
        else:
            raise ValueError("Unknown job type")
        outcome = {"result": result}
    except Exception as exc:
        outcome = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    completed.write_text(json.dumps(outcome, default=str), encoding="utf-8")


def _stop(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join()


def run_once(store: Store) -> bool:
    job = store.claim()
    if job is None:
        return False
    job_id = job["id"]
    attempt_id = job["attempts"][-1]["id"]
    write_id: str | None = None
    output = store.home / "results" / f"{job_id}.parquet"
    pending = store.home / "results" / f"{job_id}.pending.parquet"
    completed = store.home / "results" / f"{job_id}.completed.json"
    completed.unlink(missing_ok=True)
    storage: ObjectStore | None = None

    process = multiprocessing.get_context("spawn").Process(
        target=_perform, args=(store.home, store.database_url, job, completed, pending)
    )
    # 취소 확인은 100ms 단위로 유지하되 heartbeat는 주기적으로만 기록한다.
    next_heartbeat = time.monotonic() + 2
    try:
        process.start()
        while process.is_alive():
            process.join(timeout=0.1)
            if time.monotonic() >= next_heartbeat:
                store.heartbeat()
                next_heartbeat = time.monotonic() + 2
            if store.cancellation_requested(job_id):
                _stop(process)
                store.finish(job_id, "Cancelled")
                return True

        if store.cancellation_requested(job_id):
            store.finish(job_id, "Cancelled")
            return True
        if not completed.exists():
            raise RuntimeError(f"Execution process exited ({process.exitcode}) without result")
        outcome = json.loads(completed.read_text(encoding="utf-8"))
        if "error" in outcome:
            store.finish(job_id, "Failed", error=outcome["error"])
            return True

        publish = outcome["result"].get("iceberg_publish")
        if pending.exists():
            pending.replace(output)
            key = f"results/{job_id}/{attempt_id}.parquet"
            storage = ObjectStore()
            write_id = store.begin_object_write(
                location=storage.location(key), owner_kind="attempt", owner_id=attempt_id
            )
            version = storage.put(key, output)
            publish = {
                "name": f"features-{job_id[:8]}.parquet",
                "kind": "file",
                "location": storage.location(key),
                "version": version,
            }
        result = {k: v for k, v in outcome["result"].items() if k != "iceberg_publish"}
        final_status = store.finish(
            job_id, "Succeeded", result=result, publish=publish, write_id=write_id
        )
        if final_status != "Succeeded" and write_id:
            store.fail_object_write(write_id, "Job cancelled during publication")
    except Exception as exc:
        _stop(process)
        if write_id:
            try:
                store.fail_object_write(write_id, str(exc))
            except Exception:
                logging.exception("Could not record failed object write: %s", write_id)
        store.finish(job_id, "Failed", error=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        _stop(process)
        # 게시 도중 실패해도 로컬 실행 중간 파일은 남기지 않는다.
        output.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        completed.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(os.getenv("SOS_HOME", ".sos")))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=0.5)
    args = parser.parse_args()
    store = Store(args.home)
    store.initialize()
    if not store.acquire_worker():
        raise SystemExit("Another worker already owns the execution lock")

    def stop_on_sigterm(_signum, _frame):
        # 부모 종료 시 run_once의 finally가 실행 중인 자식도 종료한다.
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        store.recover()
        store.heartbeat()
        if args.once:
            run_once(store)
            return
        while True:
            store.heartbeat()
            if not run_once(store):
                time.sleep(args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        store.release_worker()


if __name__ == "__main__":
    main()
