"""SOS 내부에서 Ray Serve를 다루는 HTTP 클라이언트. Ray를 import하지 않는다.

Dashboard의 Serve REST API로 선언형 Serve Config를 적용·조회하고, 내부 Serve 경로로 추론을 중계한다.
두 포트는 Compose 내부망에만 열리며 외부 소비자는 SOS API만 사용한다.
"""

import json
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


def application_name(endpoint_id: str, revision_id: str) -> str:
    """Endpoint·판본 쌍마다 별도 애플리케이션을 둔다. 새 판본은 기존 판본과 함께 준비된다."""
    return f"{endpoint_id}--{revision_id}"


def route_prefix(endpoint_id: str, revision_id: str) -> str:
    return f"/sos/{endpoint_id}/{revision_id}"


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
