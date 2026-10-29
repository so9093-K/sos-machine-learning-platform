"""단일 Worker 잠금 아래 실행·복구·선택적 Tracking을 조합하는 프로세스 진입점."""

import argparse
import logging
import os
import signal
import time
from pathlib import Path

from .cgroup import CgroupIsolation
from .errors import RuntimeConfigurationError
from .execution_budget import ExecutionBudget
from .job_artifacts import cleanup_recovered_results, cleanup_recovered_staging
from .mlflow_config import MLflowSettings
from .mlflow_tracking import MLflowClient, run_tracking_once
from .observability import emit
from .store import Store
from .worker import cleanup_orphaned_cgroups_before_recovery, run_once


def main() -> None:
    """실행기와 Tracking은 같은 소유 세션에서 순차 실행·복구한다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(os.getenv("SOS_HOME", ".sos")))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=0.5)
    args = parser.parse_args()
    store = Store(args.home)
    store.initialize()
    try:
        mlflow_settings = MLflowSettings.from_env()
    except RuntimeConfigurationError:
        # 잘못된 선택적 Tracking 설정이 SOS의 핵심 학습 Worker를 막지 않는다.
        # 전송 대기 기록은 설정 수정·재시작 후에도 PostgreSQL에 남는다.
        emit(
            "worker.mlflow.config_invalid",
            service="sos-worker",
            level=logging.WARNING,
        )
        mlflow_settings = None
    mlflow_client = MLflowClient(mlflow_settings) if mlflow_settings else None
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
        if mlflow_client:
            store.recover_tracking_exports()
        # 단일 Worker 잠금 아래에서는 이전 실행의 자식이 남아 있지 않다.
        cleanup_recovered_results(store, interrupted)
        cleanup_recovered_staging(store)
        store.heartbeat()
        if args.once:
            run_once(store)
            if mlflow_client:
                run_tracking_once(store, mlflow_client)
            return
        while True:
            store.heartbeat()
            executed = run_once(store)
            exported = run_tracking_once(store, mlflow_client) if mlflow_client else False
            if not executed and not exported:
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
