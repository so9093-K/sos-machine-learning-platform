"""Single-process local worker for durable profiling and transformations."""

import argparse
import hashlib
import time
from pathlib import Path

from .datasets import profile, scan_dataset, transform
from .schemas import Transform
from .store import Store


def run_once(store: Store) -> bool:
    job = store.claim()
    if job is None:
        return False
    job_id = job["id"]
    output = store.home / "results" / f"{job_id}.parquet"
    pending = store.home / "results" / f"{job_id}.pending.parquet"
    try:
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
        if store.get_job(job_id)["cancel_requested"]:
            store.finish(job_id, "Cancelled")
            return True
        publish = None
        if pending.exists():
            pending.replace(output)
            version = hashlib.sha256(output.read_bytes()).hexdigest()
            publish = {"name": f"features-{job_id[:8]}.parquet",
                       "location": str(output), "version": version}
        status = store.finish(job_id, "Succeeded", result=result, publish=publish)
        if status != "Succeeded":
            output.unlink(missing_ok=True)
    except Exception as exc:
        store.finish(job_id, "Failed", error=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        pending.unlink(missing_ok=True)
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
