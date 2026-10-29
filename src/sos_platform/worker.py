"""Job 자식 프로세스의 격리·예산·중단·소유권을 감독한다.

취소 요청 시 계산 종료를 기다리지 않고 자식 프로세스를 중단한다.
종료가 확인된 뒤에만 취소를 확정한다.
"""

import ctypes
import json
import logging
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

from sqlalchemy import bindparam, text

from .cgroup import CgroupIsolation
from .diagnostics import classify_failure
from .discovery import DiscoveryPolicy
from .errors import ERRORS, PlatformError
from .execution_budget import ExecutionBudget, ExecutionLimitExceeded, process_rss_bytes
from .job_artifacts import (
    ResultPublication,
    attempt_result_paths,
    cleanup_attempt_files,
    enforce_stage_limit,
)
from .job_completion import complete_job
from .job_execution import execute_job, execution_failure_code, publication_failure_code
from .observability import emit
from .resource_admission import ExecutionCapacity, reserve_execution_memory
from .store import Store, WorkerOwnershipLost


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

    def set_stage(value: str) -> None:
        nonlocal stage
        stage = value

    try:
        # 자식 스스로 cgroup에 들어가기 전에는 Dataset 읽기·외부 Commit을 시작하지 않는다.
        CgroupIsolation.from_env(ExecutionBudget.from_env().max_rss_mib).attach_current_child(
            job["attempts"][-1]["id"]
        )
        store = Store(home, database_url=database_url)
        # 부모 Worker의 논리적 메모리 예약 안에서 실행되는 자식이다.
        store._execution_memory_reserved = True
        result = execute_job(store, job, pending, set_stage)
        outcome = {"result": result}
    except Exception as exc:
        code = execution_failure_code(job, exc, stage)
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


def cleanup_orphaned_cgroups_before_recovery(store: Store, isolation: CgroupIsolation) -> None:
    """중단된 자식이 격리 그룹에 남아 있으면 Job 복구를 진행하지 않는다."""
    if isolation.parent is None:
        return
    # Running 뿐 아니라 DB 확정 직후 부모가 사망한 완료 Attempt의 빈 그룹도 회수한다.
    # 파일시스템에 실제 남은 그룹만 조회해 Job 이력이 커져도 탐색 비용을 억제한다.
    existing = isolation.existing_attempt_ids()
    if not existing:
        return
    statement = text("SELECT id FROM attempts WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    with store.connection() as db:
        known = set(db.execute(statement, {"ids": existing}).scalars())
    if len(known) != len(existing):
        # 같은 위임 경로의 소유자를 확인할 수 없는 그룹은 임의로 삭제하지 않는다.
        from .errors import RuntimeConfigurationError

        raise RuntimeConfigurationError("Unknown Worker attempt cgroup requires operator review")
    for attempt_id in existing:
        # 자식이 남아 있으면 커널 rmdir가 거부하며 Job 복구도 중단된다.
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
    publication = ResultPublication()
    emit(
        "worker.attempt.started",
        service="sos-worker",
        job_id=job_id,
        attempt_id=attempt_id,
        kind=job["kind"],
    )
    # Claim마다 새 Attempt ID를 발급해 이전 실행의 결과 파일과 경로가 겹치지 않는다.
    # Claim 직후의 사전 파일 삭제는 불필요하며, 실패 시 Running을 남길 수 있다.
    output, pending, completed = attempt_result_paths(store, job_id, attempt_id)
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
            budget.enforce(
                elapsed_seconds=time.monotonic() - started,
                rss_bytes=process_rss_bytes(process.pid),
            )
            enforce_stage_limit(store, job, pending, budget.max_stage_mib)
            if time.monotonic() >= next_heartbeat:
                store.heartbeat()
                next_heartbeat = time.monotonic() + 2
            if store.cancellation_requested(job_id):
                store._assert_worker_ownership()
                _stop(process)
                complete_job(store, job_id, "Cancelled", attempt_id=attempt_id)
                return True

        # Worker 종료 직후 소유권을 확인한다. 유실 시 이전 Worker는
        # 취소나 성공을 확정하지 않고 새 Worker의 복구에 판단을 맡긴다.
        store._assert_worker_ownership()
        if store.cancellation_requested(job_id):
            complete_job(store, job_id, "Cancelled", attempt_id=attempt_id)
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
            complete_job(
                store,
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

        publication.prepare(store, job, outcome["result"], pending, output)
        store._assert_worker_ownership()
        final_status = complete_job(
            store,
            job_id,
            "Succeeded",
            attempt_id=attempt_id,
            result=publication.result,
            publication=publication.payload,
            write_id=publication.write_id,
        )
        if final_status != "Succeeded" and publication.write_id:
            store.fail_object_write(publication.write_id, "Job cancelled during publication")
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
        if publication.write_id:
            try:
                store.fail_object_write(publication.write_id, ERRORS["SOS-JOB-FAILED"].message)
            except Exception:
                emit(
                    "worker.write_intent.record_failed",
                    service="sos-worker",
                    level=logging.ERROR,
                    job_id=job_id,
                    attempt_id=attempt_id,
                )
        code = publication_failure_code(job, exc)
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
        complete_job(
            store,
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
        cleanup_attempt_files(store, job)
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
    return True
