"""SOS 내부에서 Ray Serve를 다루는 Config 구성과 HTTP 클라이언트. Ray를 import하지 않는다.

Dashboard의 Serve REST API로 선언형 Serve Config를 적용·조회하고, 내부 Serve 경로로 추론을 중계한다.
외부 소비자는 SOS API만 사용한다. 의도한 Config는 컨트롤러(적용)와 API(준비 판정)가 같은 함수로 만든다.
"""

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .errors import RuntimeConfigurationError


class ServingUnavailable(RuntimeError):
    """Ray Serve에 연결할 수 없거나 응답이 계약과 다르다."""


class ServingRequestRejected(RuntimeError):
    """서빙 애플리케이션이 요청을 처리하지 못했다(예: 아직 없는 경로·복제본 오류)."""

    def __init__(self, status: int):
        super().__init__(f"Serving request failed with HTTP {status}")
        self.status = status


IMPORT_PATH = "sos_platform.serving_app:build"
# 판본마다 복제본 하나. 새 판본은 기존 판본 옆에서 준비되므로 전환 중에는 복제본이 하나 더 필요하다.
REPLICA_CPUS = 0.25
FAILED_STATUSES = frozenset({"DEPLOY_FAILED", "UNHEALTHY"})
# Ray가 실제로 적용한 Config에서 의도와 비교하는 항목. Ray는 제출한 값을 그대로 돌려준다.
_COMPARED = ("import_path", "route_prefix", "args", "deployments")


def application_name(endpoint_id: str, revision_id: str) -> str:
    """Endpoint·판본 쌍마다 별도 애플리케이션을 둔다. 새 판본은 기존 판본과 함께 준비된다."""
    return f"{endpoint_id}--{revision_id}"


def route_prefix(endpoint_id: str, revision_id: str) -> str:
    return f"/sos/{endpoint_id}/{revision_id}"


def application_config(endpoint_id: str, revision: dict) -> dict:
    """Endpoint·판본 하나의 Serve 애플리케이션. 같은 판본 기록은 같은 설정을 만든다."""
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
    """서빙 의도 전체의 선언형 Serve Config. Endpoint·판본 쌍마다 애플리케이션 하나다."""
    return {
        "http_options": {"host": "0.0.0.0", "port": 8000},
        "applications": [application_config(item["endpoint_id"], item["revision"])
                         for item in applications],
    }


def config_matches(deployed: dict | None, desired: dict) -> bool:
    """Ray에 적용된 애플리케이션 Config가 의도와 같다. 직접 바뀐 Config는 같지 않다."""
    deployed = deployed or {}
    return all(deployed.get(key) == desired[key] for key in _COMPARED)


def application_status(application: dict | None, desired: dict | None) -> str:
    """Dashboard에서 관측한 배포 설정 상태. 실제 추론 가능 여부와 구별한다."""
    if application is None:
        return "NOT_DEPLOYED"
    if desired is None or not config_matches(application.get("deployed_app_config"), desired):
        return "OUT_OF_SYNC"
    return application.get("status", "UNKNOWN")


def replica_matches(answer: object, desired: dict | None) -> bool:
    """실제로 응답한 복제본의 판본·시작 지문을 대조한다. 운영 설정 상태를 추정하지 않는다."""
    return isinstance(answer, dict) and desired is not None \
        and answer.get("model_revision_id") == desired["args"]["revision"]["id"] \
        and answer.get("digest") == desired["args"]["digest"]


def replica_capacity(cpus: float) -> int:
    """주어진 CPU로 동시에 실행할 수 있는 복제본 수."""
    return math.floor(cpus / REPLICA_CPUS + 1e-9)


@dataclass(frozen=True)
class ServingClient:
    dashboard_url: str
    inference_url: str | None = None
    timeout: float = 5.0

    @classmethod
    def from_env(cls) -> "ServingClient | None":
        """서빙 Runtime이 설정되지 않은 설치에서는 None이다."""
        dashboard = os.getenv("SOS_SERVING_DASHBOARD_URL", "").strip()
        if not dashboard:
            return None
        if not dashboard.startswith(("http://", "https://")):
            raise RuntimeConfigurationError("SOS_SERVING_DASHBOARD_URL must be an HTTP URL")
        inference = os.getenv("SOS_SERVING_INFERENCE_URL", "").strip() or None
        return cls(dashboard.rstrip("/"), inference.rstrip("/") if inference else None)

    def _request(self, method: str, url: str, body: dict | None, timeout: float) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ServingRequestRejected(exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ServingUnavailable(type(exc).__name__) from exc
        try:
            return json.loads(raw) if raw else {}
        except ValueError as exc:
            raise ServingUnavailable("invalid_json") from exc

    def runtime_reachable(self) -> bool:
        """Ray head가 응답한다. 서빙할 애플리케이션이 없어 추론 Proxy가 준비되지 않았을 때의 생존 확인이다."""
        try:
            self._request("GET", f"{self.dashboard_url}/api/version", None, min(self.timeout, 2.0))
        except (ServingUnavailable, ServingRequestRejected):
            return False
        return True

    def inference_reachable(self) -> bool:
        """실제 추론 Proxy의 생존 확인. Dashboard 장애를 추론 장애로 보고하지 않는다."""
        if not self.inference_url:
            return False
        try:
            # Ray의 healthz는 JSON이 아닌 텍스트를 반환한다.
            with urllib.request.urlopen(self.inference_url + "/-/healthz",
                                        timeout=min(self.timeout, 2.0)):
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def is_ready(self, endpoint_id: str, revision_id: str, desired: dict | None) -> bool:
        """컨트롤러와 API가 실제 복제본의 준비 응답에 같은 판정을 적용한다."""
        if desired is None:
            return False
        try:
            return replica_matches(self.probe(endpoint_id, revision_id), desired)
        except (ServingUnavailable, ServingRequestRejected):
            return False

    def cpu_capacity(self) -> float:
        """살아 있는 노드의 전체 CPU. Ray State API(`ray list nodes`와 같은 정보)를 읽는다."""
        state = self._request("GET", f"{self.dashboard_url}/api/v0/nodes", None, self.timeout)
        nodes = ((state.get("data") or {}).get("result") or {}).get("result")
        if not isinstance(nodes, list):
            raise ServingUnavailable("unexpected_nodes")
        return sum(float((node.get("resources_total") or {}).get("CPU", 0))
                   for node in nodes if isinstance(node, dict) and node.get("state") == "ALIVE")

    def applications(self) -> dict[str, dict]:
        """실제 실행 상태. 애플리케이션 이름별 status·message·배포 설정을 돌려준다."""
        state = self._request("GET", f"{self.dashboard_url}/api/serve/applications/", None,
                              self.timeout)
        applications = state.get("applications")
        if not isinstance(applications, dict):
            raise ServingUnavailable("unexpected_status")
        return applications

    def apply(self, config: dict) -> None:
        """선언형 Serve Config 전체를 적용한다. 바뀌지 않은 애플리케이션은 다시 배포되지 않는다."""
        self._request("PUT", f"{self.dashboard_url}/api/serve/applications/", config,
                      self.timeout * 2)

    def probe(self, endpoint_id: str, revision_id: str) -> dict:
        """실제 복제본에 요청해 시작된 판본·설정 지문을 확인한다."""
        if not self.inference_url:
            raise ServingUnavailable("inference_url_missing")
        return self._request("GET", self.inference_url + route_prefix(endpoint_id, revision_id),
                             None, self.timeout)

    def predict(self, endpoint_id: str, revision_id: str, rows: list[list[float | None]],
                timeout: float) -> dict:
        if not self.inference_url:
            raise ServingUnavailable("inference_url_missing")
        return self._request("POST", self.inference_url + route_prefix(endpoint_id, revision_id),
                             {"rows": rows}, timeout)
