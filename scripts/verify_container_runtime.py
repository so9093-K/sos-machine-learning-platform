"""고유 Compose 프로젝트에서 인증·데이터·Worker의 컨테이너 계약을 검증한다."""

import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import isolated_compose as isolation

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ("docker", "compose", "--profile", "app", "-f", "compose.yaml")


def request(
    url: str,
    token: str | None = None,
    *,
    data: bytes | None = None,
    content_type: str = "",
    idempotency_key: str | None = None,
) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if content_type:
        headers["Content-Type"] = content_type
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = urlopen(Request(url, data=data, headers=headers), timeout=15)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        return response.status, json.loads(body) if body else {}


def await_ready(url: str, timeout: int = 150) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if request(url + "/api/ready")[0] == 200:
                return
        except (URLError, TimeoutError, OSError, ValueError):
            pass
        time.sleep(1)
    raise TimeoutError("Container API/Worker readiness did not converge")


def await_web_proxy(url: str, token: str, timeout: int = 60) -> None:
    """Web 시작과 HTTP/프록시 준비는 별도로 관찰한다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if request(url + "/api/v2/auth/me", token)[0] == 200:
                return
        except (URLError, TimeoutError, OSError, ValueError):
            pass
        time.sleep(1)
    raise TimeoutError("Container Web proxy readiness did not converge")


def compose(project: str, env: dict[str, str], *args: str) -> None:
    subprocess.run([*COMPOSE, "-p", project, *args], cwd=ROOT, env=env, check=True)


def make(env: dict[str, str], operation: str) -> None:
    """공개 Make 경로를 고유 Compose 프로젝트로 격리하여 검증한다."""
    subprocess.run(["make", operation], cwd=ROOT, env=env, check=True)


def stop_on_sigterm(_signum: int, _frame: object) -> None:
    """취소 시에도 소유 프로젝트 정리를 실행하도록 제어권을 반환한다."""
    raise KeyboardInterrupt


def main() -> int:
    """테스트 소유 프로젝트에서 쓰기·인증·복구를 검증하고 해당 볼륨만 정리한다."""
    previous = signal.signal(signal.SIGTERM, stop_on_sigterm)
    project = "sos-e2e-" + secrets.token_hex(6)
    ports = isolation.free_ports()
    _, _, api_port, web_port = ports
    env = isolation.isolated_environment(os.environ, project, ports)
    viewer = env["SOS_E2E_VIEWER_TOKEN"]
    researcher = env["SOS_E2E_RESEARCHER_TOKEN"]
    runtime = isolation.create_owner(project, ports)
    url = f"http://127.0.0.1:{api_port}"
    step = "configuration"
    try:
        compose(project, env, "config", "--quiet")
        step = "public make up/status/doctor"
        make(env, "up")
        make(env, "status")
        make(env, "doctor")
        await_ready(url)
        step = "unauthenticated access"
        assert request(url + "/api/v2/auth/me")[0] == 401
        step = "researcher identity"
        status, actor = request(url + "/api/v2/auth/me", researcher)
        assert status == 200 and actor["actor_id"] == "ci-researcher"
        step = "web proxy"
        await_web_proxy(f"http://127.0.0.1:{web_port}", researcher)
        step = "viewer dataset read"
        assert request(url + "/api/datasets", viewer)[0] == 200

        boundary = "sos-container-" + secrets.token_hex(7)
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="container.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
            "timestamp,value\n1,10\n2,20\n"
            f"\r\n--{boundary}--\r\n"
        ).encode()
        upload = url + "/api/datasets/files"
        mime = f"multipart/form-data; boundary={boundary}"
        step = "viewer write rejected"
        denied, _ = request(upload, viewer, data=body, content_type=mime)
        assert denied == 403, f"viewer write response: {denied}"
        step = "researcher upload"
        status, dataset = request(upload, researcher, data=body, content_type=mime)
        assert status == 201, f"dataset upload response: {status}"
        dataset_id = dataset["id"]
        step = "cross-actor dataset isolation"
        denied, _ = request(url + f"/api/datasets/{dataset_id}", viewer)
        assert denied == 404, f"cross-actor dataset access: {denied}"
        status, viewer_page = request(url + "/api/datasets", viewer)
        assert status == 200 and not any(row["id"] == dataset_id for row in viewer_page["items"])

        step = "Iceberg job submission"
        table = "container_" + secrets.token_hex(5)
        payload = json.dumps({"dataset_id": dataset_id, "table_name": table}).encode()
        # Token 모드의 Job 제출은 멱등성 키가 없으면 실패해야 한다.
        denied, _ = request(
            url + "/api/iceberg/import",
            researcher,
            data=payload,
            content_type="application/json",
        )
        assert denied == 428, f"missing idempotency key: {denied}"
        status, job = request(
            url + "/api/iceberg/import",
            researcher,
            data=payload,
            content_type="application/json",
            idempotency_key=secrets.token_hex(16),
        )
        assert status in (200, 201, 202), (status, job)
        step = "Iceberg job execution"
        limit = time.monotonic() + 150
        while time.monotonic() < limit:
            status, result = request(url + "/api/jobs/" + job["id"], researcher)
            assert status == 200
            if result["status"] == "Succeeded":
                break
            if result["status"] in ("Failed", "Cancelled"):
                raise AssertionError(f"Worker returned {result['status']}")
            time.sleep(1)
        else:
            raise TimeoutError("Worker did not complete Iceberg import")

        step = "restart and authentication rotation"
        former_researcher = researcher
        researcher = secrets.token_hex(32)
        identities = json.loads(env["SOS_AUTH_TOKEN_HASHES"])
        old_digest = hashlib.sha256(former_researcher.encode()).hexdigest()
        identities[hashlib.sha256(researcher.encode()).hexdigest()] = identities.pop(old_digest)
        env["SOS_AUTH_TOKEN_HASHES"] = json.dumps(identities)
        make(env, "restart")
        await_ready(url)
        assert request(url + "/api/v2/auth/me", former_researcher)[0] == 401
        assert request(url + "/api/v2/auth/me", researcher)[0] == 200
        status, listing = request(url + "/api/datasets", researcher)
        assert status == 200 and any(row["id"] == dataset_id for row in listing["items"])
        step = "post-restart Iceberg snapshots"
        status, snapshots = request(url + f"/api/iceberg/{table}/snapshots", researcher)
        assert status == 200 and snapshots

        # restart는 컨테이너를 재생성하지 않으므로, named volume 보존을 별도로 검증한다.
        step = "full down/up data preservation"
        make(env, "down")
        make(env, "up")
        await_ready(url)
        status, listing = request(url + "/api/datasets", researcher)
        assert status == 200 and any(row["id"] == dataset_id for row in listing["items"])
        denied, _ = request(url + f"/api/datasets/{dataset_id}", viewer)
        assert denied == 404
        status, snapshots = request(url + f"/api/iceberg/{table}/snapshots", researcher)
        assert status == 200 and snapshots
        print("Container contract: Make, RBAC, isolation, Job, Iceberg, restart and down/up passed")
        return 0
    except (
        AssertionError,
        TimeoutError,
        URLError,
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        KeyboardInterrupt,
    ) as exc:
        print(
            f"Container verification failed at {step}: {type(exc).__name__} {exc}",
            file=sys.stderr,
        )
        subprocess.run([*COMPOSE, "-p", project, "ps"], cwd=ROOT, env=env, check=False)
        return 1
    finally:
        # 소유 증거를 확인한 테스트 프로젝트만 정리하며 외부 볼륨은 보존한다.
        cleanup = isolation.clean_owned(project, runtime, env)
        signal.signal(signal.SIGTERM, previous)
        if cleanup:
            raise RuntimeError("Test-owned Compose resources could not be cleaned")


if __name__ == "__main__":
    sys.exit(main())
