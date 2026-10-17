"""EDA 및 변환 작업을 별도 프로세스로 격리한다.

취소 요청 시 계산 종료를 기다리지 않고 자식 프로세스를 중단한다.
종료가 확인된 뒤에만 취소를 확정한다.
"""

import argparse
import ctypes
import json
import logging
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

from sqlalchemy import text

from .cgroup import CgroupIsolation
from .datasets import (
    cleanup_iceberg_staging,
    iceberg_staging_bytes,
    import_to_iceberg,
    pinned_scan_dataset,
)
from .diagnostics import classify_failure
from .discovery import DiscoveryPolicy
from .discovery import explore as discovery_explore
from .discovery import profile as discovery_profile
from .errors import ERRORS, PlatformError
from .execution_budget import ExecutionBudget, ExecutionLimitExceeded, process_rss_bytes
from .object_store import ObjectStore
from .observability import emit
from .processing import transform
from .resource_admission import ExecutionCapacity, reserve_execution_memory
from .schemas import ExplorationSpec, Transform
from .store import Store, WorkerOwnershipLost
from .understanding import build_understanding


def _guard_child_parent(parent_pid: int) -> None:
    """Linux에서 Worker 부모가 강제 종료되면 자식도 커밋을 계속하지 못하게 한다."""
    if sys.platform != "linux":
        # 다른 OS에는 같은 강제 종료 보장을 제공하지 않는다.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # 부모가 fork/spawn과 prctl 등록 사이에 종료되는 경합도 차단한다.
    if os.getppid() != parent_pid:
        os._exit(1)


def _perform(
    home: Path, database_url: str, job: dict, completed: Path, pending: Path, parent_pid: int
) -> None:
    """계산은 자식 프로세스에서 수행하고 결과 확정은 부모가 담당한다."""
    # 부모 SIGKILL 시 남은 자식이 Iceberg Commit/파일 게시를 진행하면
    # 새 Worker가 같은 Job을 복구한 뒤에도 실행이 중복될 수 있다.
    _guard_child_parent(parent_pid)
    stage = "read_input"

    def mark_commit_started() -> None:
        nonlocal stage
        stage = "iceberg_commit"

    try:
        # 자식 스스로 cgroup에 들어가기 전에는 Dataset 읽기·외부 Commit을 시작하지 않는다.
        CgroupIsolation.from_env(ExecutionBudget.from_env().max_rss_mib).attach_current_child(
            job["attempts"][-1]["id"]
        )
        store = Store(home, database_url=database_url)
        # 부모 Worker의 논리적 메모리 예약 안에서 실행되는 자식이다.
        store._execution_memory_reserved = True
        dataset = store.get_dataset(job["dataset_id"])
        if dataset is None:
            raise ValueError("Input dataset is missing")
        if job["kind"] == "profile":
            stage = "processing"
            if job["spec"].get("mode") == "understanding":
                result = build_understanding(
                    store, dataset, scope=job["spec"].get("scope", "sample")
                )
            else:
                result = discovery_profile(store, dataset, sample=None)
        elif job["kind"] == "exploration":
            stage = "processing"
            spec = ExplorationSpec.model_validate(job["spec"]["exploration"])
            result = discovery_explore(
                store, dataset, spec.column, spec.metric, spec.group_by, scope="full"
            )
        elif job["kind"] == "transform":
            stage = "processing"
            spec = Transform.model_validate(job["spec"]["transform"])
            with pinned_scan_dataset(store, dataset) as source:
                lf = transform(source, spec)
                lf.sink_parquet(pending)
            result = {"input_dataset_id": dataset["id"]}
        elif job["kind"] == "iceberg_import":
            stage = "iceberg_prepare"
            result = {
                "iceberg_publish": import_to_iceberg(
                    store,
                    dataset,
                    job["spec"]["table_name"],
                    job["attempts"][-1]["id"],
                    on_commit_start=mark_commit_started,
                )
            }
        else:
            raise ValueError("Unknown job type")
        outcome = {"result": result}
    except Exception as exc:
        code = (
            "SOS-ICEBERG-UNCERTAIN"
            if job["kind"] == "iceberg_import" and stage == "iceberg_commit"
            else exc.code
            if isinstance(exc, PlatformError)
            else "SOS-JOB-FAILED"
        )
        category, exception_type = classify_failure(exc)
        outcome = {
            "error_code": code,
            "failure_stage": stage,
            "failure_category": category,
            "exception_type": exception_type,
        }
    completed.write_text(json.dumps(outcome, default=str), encoding="utf-8")


def _stop(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join()


def attempt_result_paths(store: Store, job_id: str, attempt_id: str) -> tuple[Path, Path, Path]:
    """Attempt마다 고유한 로컬 계산 결과 경로를 반환한다."""
    # Job/Attempt ID는 PostgreSQL에서 발급한 UUID다.
    root = store.home / "results"
    stem = f"{job_id}.{attempt_id}"
    return (
        root / f"{stem}.parquet",
        root / f"{stem}.pending.parquet",
        root / f"{stem}.completed.json",
    )


def cleanup_recovered_results(store: Store, interrupted: list[tuple[str, str]]) -> None:
    """복구가 확정된 Attempt의 로컬 파일만 회수하고 S3·공유 객체는 건드리지 않는다."""
    for job_id, attempt_id in interrupted:
        for path in attempt_result_paths(store, job_id, attempt_id):
            path.unlink(missing_ok=True)


def cleanup_orphaned_cgroups_before_recovery(store: Store, isolation: CgroupIsolation) -> None:
    """중단된 자식이 격리 그룹에 남아 있으면 Job 복구를 진행하지 않는다."""
    if isolation.parent is None:
        return
    with store.connection() as db:
        attempts = db.execute(
            text("SELECT id FROM attempts WHERE status='Running'")
        ).scalars().all()
    for attempt_id in attempts:
        # 아직 프로세스가 속해 있으면 커널 rmdir가 실패해 복구를 중단한다.
        isolation.cleanup(attempt_id)


def run_once(store: Store) -> bool:
    """예약 가능한 실행 용량을 확보한 뒤에만 Job을 Running으로 변경한다."""
    budget = ExecutionBudget.from_env()
    # required 모드에서 위임·쓰기 권한이 없으면 Attempt를 Claim하기 전에 거부한다.
    cgroup = CgroupIsolation.from_env(budget.max_rss_mib)
    cgroup.validate_before_claim()
    capacity = ExecutionCapacity.from_env()
    capacity.validate_worker_budget(
        worker_rss_mib=budget.max_rss_mib,
        discovery_memory_mib=DiscoveryPolicy.from_env().memory_mib,
    )
    try:
        with reserve_execution_memory(store, budget.max_rss_mib, capacity):
            return _run_once_reserved(store, budget, cgroup)
    except PlatformError as exc:
        if exc.code == "SOS-EXECUTION-CAPACITY":
            # Job을 Claim하지 않았으므로 Queued 상태를 유지하고 다음 폴링에서 재시도한다.
            return False
        raise


def _run_once_reserved(store: Store, budget: ExecutionBudget, cgroup: CgroupIsolation) -> bool:
    job = store.claim()
    if job is None:
        return False
    job_id = job["id"]
    attempt_id = job["attempts"][-1]["id"]
    write_id: str | None = None
    emit(
        "worker.attempt.started",
        service="sos-worker",
        job_id=job_id,
        attempt_id=attempt_id,
        kind=job["kind"],
    )
    # Worker가 강제 종료돼도 이전 Attempt의 로컬 결과를 새 실행이 재사용하지 않는다.
    output, pending, completed = attempt_result_paths(store, job_id, attempt_id)
    completed.unlink(missing_ok=True)
    process = multiprocessing.get_context("spawn").Process(
        target=_perform,
        args=(store.home, store.database_url, job, completed, pending, os.getpid()),
    )
    # 취소 확인은 100ms 단위로 유지하되 heartbeat는 주기적으로만 기록한다.
    next_heartbeat = time.monotonic() + 2
    try:
        process.start()
        started = time.monotonic()
        while process.is_alive():
            process.join(timeout=0.1)
            stage_bytes = (
                iceberg_staging_bytes(store, attempt_id)
                if job["kind"] == "iceberg_import"
                else None
            )
            budget.enforce(
                elapsed_seconds=time.monotonic() - started,
                stage_bytes=stage_bytes,
                rss_bytes=process_rss_bytes(process.pid),
            )
            if time.monotonic() >= next_heartbeat:
                store.heartbeat()
                next_heartbeat = time.monotonic() + 2
            if store.cancellation_requested(job_id):
                store._assert_worker_ownership()
                _stop(process)
                store.finish(job_id, "Cancelled", attempt_id=attempt_id)
                return True

        # Worker 종료 직후 소유권을 확인한다. 유실 시 이전 Worker는
        # 취소나 성공을 확정하지 않고 새 Worker의 복구에 판단을 맡긴다.
        store._assert_worker_ownership()
        if store.cancellation_requested(job_id):
            store.finish(job_id, "Cancelled", attempt_id=attempt_id)
            return True
        if not completed.exists():
            if cgroup.oom_killed(attempt_id):
                raise ExecutionLimitExceeded("SOS-JOB-MEMORY-LIMIT", "memory")
            raise RuntimeError(f"Execution process exited ({process.exitcode}) without result")
        outcome = json.loads(completed.read_text(encoding="utf-8"))
        if "error_code" in outcome:
            code = outcome["error_code"]
            emit(
                "worker.attempt.failed",
                service="sos-worker",
                level=logging.ERROR,
                job_id=job_id,
                attempt_id=attempt_id,
                error_code=code,
                stage=outcome.get("failure_stage"),
                exception_type=outcome.get("exception_type"),
                failure_category=outcome.get("failure_category"),
            )
            store.finish(
                job_id,
                "Failed",
                attempt_id=attempt_id,
                error=ERRORS[code].message,
                error_code=code,
                failure_stage=outcome.get("failure_stage"),
                failure_category=outcome.get("failure_category"),
                exception_type=outcome.get("exception_type"),
            )
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
        store._assert_worker_ownership()
        final_status = store.finish(
            job_id,
            "Succeeded",
            attempt_id=attempt_id,
            result=result,
            publish=publish,
            write_id=write_id,
        )
        if final_status != "Succeeded" and write_id:
            store.fail_object_write(write_id, "Job cancelled during publication")
        emit(
            "worker.attempt.finished",
            service="sos-worker",
            job_id=job_id,
            attempt_id=attempt_id,
            status=final_status,
        )
    except WorkerOwnershipLost:
        # 소유권을 잃었을 때 기존 Worker가 Failed/Cancelled를 확정하는 것도 위험하다.
        # 새 소유자가 동일 DB의 Running Attempt를 보수적으로 복구한다.
        _stop(process)
        emit(
            "worker.attempt.failed",
            service="sos-worker",
            level=logging.ERROR,
            job_id=job_id,
            attempt_id=attempt_id,
            error_code="SOS-JOB-INTERRUPTED",
            stage="execution",
            exception_type="WorkerOwnershipLost",
            failure_category="process_exit",
        )
        raise
    except Exception as exc:
        _stop(process)
        # 잠금이 사라진 Worker는 실패 확정이나 게시 의도 변경도 수행하지 않는다.
        # DB가 살아 있으면 새 소유자가 Running Attempt의 복구를 맡는다.
        store._assert_worker_ownership()
        if write_id:
            try:
                store.fail_object_write(write_id, ERRORS["SOS-JOB-FAILED"].message)
            except Exception:
                emit(
                    "worker.write_intent.record_failed",
                    service="sos-worker",
                    level=logging.ERROR,
                    job_id=job_id,
                    attempt_id=attempt_id,
                )
        if job["kind"] == "iceberg_import":
            # 부모는 자식의 Catalog 커밋 진입 시점을 확정할 수 없다.
            code = "SOS-ICEBERG-UNCERTAIN"
        elif isinstance(exc, ExecutionLimitExceeded):
            code = exc.code
        else:
            code = "SOS-JOB-FAILED"
        if isinstance(exc, ExecutionLimitExceeded):
            category, exception_type = exc.category, type(exc).__name__
        else:
            category, exception_type = classify_failure(exc)
        emit(
            "worker.attempt.failed",
            service="sos-worker",
            level=logging.ERROR,
            job_id=job_id,
            attempt_id=attempt_id,
            error_code=code,
            stage="execution" if isinstance(exc, ExecutionLimitExceeded) else "publication",
            exception_type=exception_type,
            failure_category=category,
        )
        store.finish(
            job_id,
            "Failed",
            attempt_id=attempt_id,
            error=ERRORS[code].message,
            error_code=code,
            failure_stage="execution" if isinstance(exc, ExecutionLimitExceeded) else "publication",
            failure_category=category,
            exception_type=exception_type,
        )
    finally:
        _stop(process)
        # 게시 도중 실패해도 로컬 실행 중간 파일은 남기지 않는다.
        output.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        completed.unlink(missing_ok=True)
        try:
            cgroup.cleanup(attempt_id)
        except (OSError, ValueError) as exc:
            # 커널이 아직 하위 프로세스를 회수하지 않았으면 격리 실패로 숨기지 않는다.
            emit(
                "worker.cgroup.cleanup_failed",
                service="sos-worker",
                level=logging.ERROR,
                job_id=job_id,
                attempt_id=attempt_id,
                exception_type=type(exc).__name__,
            )
        if job["kind"] == "iceberg_import":
            # 자식이 SIGKILL되어도 Attempt에 속한 미완성 입력은 부모가 회수한다.
            try:
                cleanup_iceberg_staging(store, attempt_id)
            except OSError:
                # 정리 실패로 이미 확정한 Job/Attempt 결과를 덮어쓰지 않는다.
                emit(
                    "worker.staging.cleanup_failed",
                    service="sos-worker",
                    level=logging.ERROR,
                    job_id=job_id,
                    attempt_id=attempt_id,
                )
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
        isolation = CgroupIsolation.from_env(ExecutionBudget.from_env().max_rss_mib)
        isolation.validate_before_claim()
        cleanup_orphaned_cgroups_before_recovery(store, isolation)
        interrupted = store.recover()
        # 단일 Worker 잠금 아래에서는 이전 실행의 자식이 남아 있지 않다.
        cleanup_recovered_results(store, interrupted)
        cleanup_iceberg_staging(store)
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
        try:
            store.release_worker()
        finally:
            store.engine.dispose()


if __name__ == "__main__":
    main()
