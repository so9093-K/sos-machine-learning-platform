"""고유 Compose 프로젝트에서 인증·데이터·Worker의 컨테이너 계약을 검증한다."""

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ("docker", "compose", "-f", "compose.yaml", "-f", "compose.app.yaml")


def free_ports() -> list[int]:
    sockets = [socket.socket() for _ in range(4)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def request(
    url: str,
    token: str | None = None,
    *,
    data: bytes | None = None,
    content_type: str = "",
    idempotency_key: str | None = None
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
        except (URLError, TimeoutError, ValueError):
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


def main() -> int:
    """테스트 소유 프로젝트에서 쓰기·인증·복구를 검증하고 해당 볼륨만 정리한다."""
    project = "sos-container-e2e-" + secrets.token_hex(6)
    db_port, s3_port, api_port, web_port = free_ports()
    viewer, researcher = secrets.token_hex(32), secrets.token_hex(32)
    subjects = {
        hashlib.sha256(token.encode()).hexdigest(): {
            "actor_id": f"container-{role}",
            "kind": "human",
            "roles": [role],
        }
        for role, token in (("viewer", viewer), ("researcher", researcher))
    }
    # 부모의 외부 DB·버킷·인증 설정을 상속하지 않는다.
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOS_")}
    env.update(
        SOS_AUTH_TOKEN_HASHES=json.dumps(subjects),
        SOS_DB_PASSWORD=secrets.token_urlsafe(24),
        SOS_S3_ACCESS_KEY="sos-container-test",
        SOS_S3_SECRET_KEY=secrets.token_urlsafe(24),
        SOS_DB_PORT=str(db_port),
        SOS_S3_PORT=str(s3_port),
        SOS_API_PORT=str(api_port),
        SOS_WEB_PORT=str(web_port),
        SOS_PYTHON_IMAGE=f"sos-python-e2e:{project}",
        SOS_WEB_IMAGE=f"sos-web-e2e:{project}",
    )
    url = f"http://127.0.0.1:{api_port}"
    step = "configuration"
    try:
        compose(project, env, "config", "--quiet")
        compose(project, env, "up", "--build", "-d")
        await_ready(url)
        step = "unauthenticated access"
        assert request(url + "/api/v2/auth/me")[0] == 401
        step = "researcher identity"
        status, actor = request(url + "/api/v2/auth/me", researcher)
        assert status == 200 and actor["actor_id"] == "container-researcher"
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

        step = "restart and data preservation"
        compose(project, env, "restart", "api", "worker", "web")
        await_ready(url)
        status, listing = request(url + "/api/datasets", researcher)
        assert status == 200 and any(row["id"] == dataset_id for row in listing["items"])
        step = "post-restart Iceberg snapshots"
        status, snapshots = request(url + f"/api/iceberg/{table}/snapshots", researcher)
        assert status == 200 and snapshots
        print("Container contract: Token RBAC, Web proxy, Dataset, Worker, Iceberg, restart passed")
        return 0
    except (
        AssertionError,
        TimeoutError,
        URLError,
        ValueError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        print(
            f"Container verification failed at {step}: {type(exc).__name__} {exc}",
            file=sys.stderr,
        )
        subprocess.run([*COMPOSE, "-p", project, "ps"], cwd=ROOT, env=env, check=False)
        return 1
    finally:
        # 유일한 임의 프로젝트만 정리한다. 기존 sos-platform 볼륨은 절대 삭제하지 않는다.
        subprocess.run(
            [*COMPOSE, "-p", project, "down", "--volumes", "--remove-orphans"],
            cwd=ROOT,
            env=env,
            check=False,
        )


if __name__ == "__main__":
    sys.exit(main())
