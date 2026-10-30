"""서빙 배포 의도(PostgreSQL)를 Ray Serve 실제 상태에 맞추는 전용 프로세스.

Job Worker·MLflow 전송과 분리해 그 장애가 온라인 서빙 복구를 막지 않게 한다. 재조정은 멱등이다.
의도 전체를 선언형 Serve Config로 적용하고(바뀌지 않은 애플리케이션은 다시 배포되지 않는다),
전환 대상은 그 경로의 실제 복제본이 의도한 설정 지문으로 응답할 때만 조건부 갱신으로
활성 판본에 올린다.
"""

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .observability import emit
from .serving_client import (
    FAILED_STATUSES,
    ServingClient,
    ServingRequestRejected,
    ServingUnavailable,
    application_name,
    config_matches,
    desired_config,
)
from .store import Store

ReconcileOutcome = Literal["converged", "applied", "unavailable"]
# Ray가 응답하지 않으면 재시도 간격을 늘려 로그와 연결 시도를 줄인다.
OUTAGE_PAUSE_SECONDS = 10.0


@dataclass
class ReconcileState:
    """조정 사이의 기억. Ray가 재시작해 잃은 상태는 실제 상태 조회로 다시 맞춘다."""

    applied: dict | None = None
    # 실패를 이미 알린 애플리케이션. 상태가 바뀔 때만 로그를 남긴다.
    failed: set[str] = field(default_factory=set)


def drifted(current: dict[str, dict], config: dict) -> bool:
    """Ray에 적용된 Config가 의도와 다르다. Dashboard 등에서 직접 바꾼 Config도 의도로 되돌린다."""
    return any(not config_matches((current.get(app["name"]) or {}).get("deployed_app_config"), app)
               for app in config["applications"])


def reconcile_once(store: Store, client: ServingClient, state: ReconcileState) -> ReconcileOutcome:
    """의도와 실제 상태가 다르면 의도를 적용하고, 준비된 전환 대상을 승격한다.

    애플리케이션이 사라졌거나(Ray 재시작) 적용된 Config가 의도와 다르면 다시 적용한다.
    """
    applications = store.serving_applications()
    config = desired_config(applications)
    try:
        current = client.applications()
        names = {app["name"] for app in config["applications"]}
        outcome: ReconcileOutcome = "converged"
        if set(current) != names or state.applied != config or drifted(current, config):
            client.apply(config)
            state.applied, outcome = config, "applied"
            emit("serving.config.applied", service="sos-serving-controller")
    except (ServingUnavailable, ServingRequestRejected) as exc:
        emit("serving.reconcile.unavailable", service="sos-serving-controller",
             level=logging.WARNING, failure_type=type(exc).__name__,
             status=str(exc.status) if isinstance(exc, ServingRequestRejected) else None)
        state.applied = None
        return "unavailable"
    failed = set()
    for item in applications:
        name = application_name(item["endpoint_id"], item["revision"]["id"])
        status = current.get(name, {}).get("status")
        if status in FAILED_STATUSES:
            failed.add(name)
            if name not in state.failed:
                # 원인 전체는 Ray Dashboard·소유자 API에서 본다. 로그에는 식별자와 상태만 남긴다.
                emit("serving.application.failed", service="sos-serving-controller",
                     level=logging.WARNING, endpoint_id=item["endpoint_id"],
                     model_revision_id=item["revision"]["id"], status=status)
    state.failed = failed
    desired = {app["name"]: app for app in config["applications"]}
    endpoints = {item["endpoint_id"] for item in applications}
    for endpoint_id in endpoints:
        endpoint = store.get_serving_endpoint(endpoint_id)
        pending = endpoint and endpoint["pending_model_revision_id"]
        if not pending:
            continue
        name = application_name(endpoint_id, pending)
        if current.get(name, {}).get("status") == "RUNNING" \
                and client.is_ready(endpoint_id, pending, desired.get(name)) \
                and store.promote_serving_revision(endpoint_id, pending):
            emit("serving.revision.activated", service="sos-serving-controller",
                 endpoint_id=endpoint_id, model_revision_id=pending)
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(os.getenv("SOS_HOME", ".sos")))
    parser.add_argument("--poll", type=float, default=2.0)
    args = parser.parse_args()
    client = ServingClient.from_env()
    if client is None:
        raise SystemExit("SOS_SERVING_DASHBOARD_URL is not configured")
    store = Store(args.home)
    state = ReconcileState()
    try:
        while True:
            outcome = reconcile_once(store, client, state)
            time.sleep(OUTAGE_PAUSE_SECONDS if outcome == "unavailable" else args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    main()
