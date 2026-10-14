"""Isolated local execution for profiling and materialization.

Each job runs in a child process so a cancellation can interrupt Polars,
rather than merely discarding results after an expensive calculation finishes.
"""

import argparse
import hashlib
import json
import multiprocessing
import time
from pathlib import Path

from .datasets import profile, scan_dataset, transform
from .schemas import Transform
from .store import Store


def _perform(home: Path, job: dict, completed: Path, pending: Path, output: Path) -> None:
    """Compute in a separate process; publish results only in the parent."""
    try:
        store = Store(home)
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
            result = {"artifact": str(output), "input_dataset_id": dataset["id"]}
        else:
            raise ValueError("Unknown job type")
        outcome = {"result": result}
    except Exception as exc:
        outcome = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    completed.write_text(json.dumps(outcome, default=str), encoding="utf-8")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    output = store.home / "results" / f"{job_id}.parquet"
    pending = store.home / "results" / f"{job_id}.pending.parquet"
    completed = store.home / "results" / f"{job_id}.completed.json"
    completed.unlink(missing_ok=True)

    process = multiprocessing.get_context("spawn").Process(
        target=_perform, args=(store.home, job, completed, pending, output)
    )
    try:
        process.start()
        while process.is_alive():
            process.join(timeout=0.1)
            if store.get_job(job_id)["cancel_requested"]:
                _stop(process)
                store.finish(job_id, "Cancelled")
                return True

        if store.get_job(job_id)["cancel_requested"]:
            store.finish(job_id, "Cancelled")
            return True
        if not completed.exists():
            raise RuntimeError(f"Execution process exited ({process.exitcode}) without result")
        outcome = json.loads(completed.read_text(encoding="utf-8"))
        if "error" in outcome:
            store.finish(job_id, "Failed", error=outcome["error"])
            return True

        publish = None
        if pending.exists():
            pending.replace(output)
            publish = {
                "name": f"features-{job_id[:8]}.parquet",
                "location": str(output),
                "version": _digest(output),
            }
        status = store.finish(job_id, "Succeeded", result=outcome["result"], publish=publish)
        if status != "Succeeded":
            output.unlink(missing_ok=True)
    except Exception as exc:
        _stop(process)
        store.finish(job_id, "Failed", error=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        _stop(process)
        pending.unlink(missing_ok=True)
        completed.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(".sos"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=0.5)
    args = parser.parse_args()
    store = Store(args.home)
    store.initialize()
    store.recover()  # Only one local worker process may own this storage root.
    if args.once:
        run_once(store)
        return
    try:
        while True:
            if not run_once(store):
                time.sleep(args.poll)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
