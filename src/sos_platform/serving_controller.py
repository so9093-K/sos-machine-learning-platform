"""서빙 배포 의도(PostgreSQL)를 Ray Serve 실제 상태에 맞추는 전용 프로세스.

Job Worker·MLflow 전송과 분리해 그 장애가 온라인 서빙 복구를 막지 않게 한다. 재조정은 멱등이다.
의도 전체를 선언형 Serve Config로 적용하고(바뀌지 않은 애플리케이션은 다시 배포되지 않는다),
전환 대상은 그 경로의 실제 복제본이 의도한 설정 지문으로 응답할 때만 조건부 갱신으로
활성 판본에 올린다.
"""

import argparse
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

from .observability import emit
from .serving_client import (
    ServingClient,
    ServingRequestRejected,
    ServingUnavailable,
    application_name,
    route_prefix,
)
from .store import Store

ReconcileOutcome = Literal["converged", "applied", "unavailable"]
# Ray가 응답하지 않으면 재시도 간격을 늘려 로그와 연결 시도를 줄인다.
OUTAGE_PAUSE_SECONDS = 10.0
REPLICA_CPUS = 0.25


IMPORT_PATH = "sos_platform.serving_app:build"


def _application(endpoint_id: str, revision: dict) -> dict:
    deployments = [{"name": "model", "num_replicas": 1,
                    "ray_actor_options": {"num_cpus": REPLICA_CPUS}}]
    # 판본 기록·배포 설정이 바뀌면 지문도 바뀐다. 복제본은 시작할 때 받은 지문을 그대로 알린다.
    digest = hashlib.sha256(json.dumps(
        {"import_path": IMPORT_PATH, "revision": revision, "deployments": deployments},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {
        "name": application_name(endpoint_id, revision["id"]),
        "route_prefix": route_prefix(endpoint_id, revision["id"]),
        "import_path": IMPORT_PATH,
        "args": {"revision": revision, "digest": digest},
        "deployments": deployments,
    }


def desired_config(applications: list[dict]) -> dict:
    """Endpoint·판본 쌍마다 하나의 애플리케이션. 같은 판본 기록은 같은 설정을 만든다."""
    return {
        "http_options": {"host": "0.0.0.0", "port": 8000},
        "applications": [_application(item["endpoint_id"], item["revision"])
                         for item in applications],
    }


def serves_as_desired(client: ServingClient, endpoint_id: str, revision_id: str,
                      desired: dict | None) -> bool:
    """그 경로의 실제 복제본이 의도한 설정 지문으로 응답해야 준비된 것으로 본다."""
    if desired is None:
        return False
    try:
        answer = client.probe(endpoint_id, revision_id)
    except (ServingUnavailable, ServingRequestRejected):
        return False
    return answer.get("model_revision_id") == revision_id \
        and answer.get("digest") == desired["args"]["digest"]


def reconcile_once(store: Store, client: ServingClient,
                   applied: dict | None) -> tuple[ReconcileOutcome, dict | None]:
    """의도와 실제 상태가 다르면 의도를 적용하고, 준비된 전환 대상을 승격한다.

    Returns:
        결과와 마지막으로 적용한 설정. Ray가 재시작해 애플리케이션을 잃으면 다시 적용한다.
    """
    applications = store.serving_applications()
    config = desired_config(applications)
    try:
        current = client.applications()
        names = {app["name"] for app in config["applications"]}
        outcome: ReconcileOutcome = "converged"
        if set(current) != names or applied != config:
            client.apply(config)
            applied, outcome = config, "applied"
    except (ServingUnavailable, ServingRequestRejected) as exc:
        emit("serving.reconcile.unavailable", service="sos-serving-controller",
             level=logging.WARNING, failure_type=type(exc).__name__,
             status=str(exc.status) if isinstance(exc, ServingRequestRejected) else None)
        return "unavailable", None
    desired = {app["name"]: app for app in config["applications"]}
    endpoints = {item["endpoint_id"] for item in applications}
    for endpoint_id in endpoints:
        endpoint = store.get_serving_endpoint(endpoint_id)
        pending = endpoint and endpoint["pending_model_revision_id"]
        if not pending:
            continue
        name = application_name(endpoint_id, pending)
        if current.get(name, {}).get("status") == "RUNNING" \
                and serves_as_desired(client, endpoint_id, pending, desired.get(name)) \
                and store.promote_serving_revision(endpoint_id, pending):
            emit("serving.revision.activated", service="sos-serving-controller",
                 endpoint_id=endpoint_id, model_revision_id=pending)
    return outcome, applied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path(os.getenv("SOS_HOME", ".sos")))
    parser.add_argument("--poll", type=float, default=2.0)
    args = parser.parse_args()
    client = ServingClient.from_env()
    if client is None:
        raise SystemExit("SOS_SERVING_DASHBOARD_URL is not configured")
    store = Store(args.home)
    applied: dict | None = None
    try:
        while True:
            outcome, applied = reconcile_once(store, client, applied)
            time.sleep(OUTAGE_PAUSE_SECONDS if outcome == "unavailable" else args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    main()
