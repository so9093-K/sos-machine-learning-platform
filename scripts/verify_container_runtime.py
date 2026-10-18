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
    url: str, token: str | None = None, *, data: bytes | None = None, content_type: str = ""
) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if content_type:
        headers["Content-Type"] = content_type
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
    try:
        compose(project, env, "config", "--quiet")
        compose(project, env, "up", "--build", "-d")
        await_ready(url)
        assert request(url + "/api/v2/auth/me")[0] == 401
        status, actor = request(url + "/api/v2/auth/me", researcher)
        assert status == 200 and actor["actor_id"] == "container-researcher"
        assert request(f"http://127.0.0.1:{web_port}/api/v2/auth/me", researcher)[0] == 200
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
        assert request(upload, viewer, data=body, content_type=mime)[0] == 403
        status, dataset = request(upload, researcher, data=body, content_type=mime)
        assert status == 201
        dataset_id = dataset["id"]

        table = "container_" + secrets.token_hex(5)
        status, job = request(
            url + "/api/iceberg/import",
            researcher,
            data=json.dumps({"dataset_id": dataset_id, "table_name": table}).encode(),
            content_type="application/json",
        )
        assert status in (200, 201, 202), (status, job)
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

        compose(project, env, "restart", "api", "worker", "web")
        await_ready(url)
        status, listing = request(url + "/api/datasets", researcher)
        assert status == 200 and any(row["id"] == dataset_id for row in listing["items"])
        status, snapshots = request(url + f"/api/iceberg/{table}/snapshots", researcher)
        assert status == 200 and snapshots
        print("Container contract: Token RBAC, Web proxy, Dataset, Worker, Iceberg, restart passed")
        return 0
    except (AssertionError, TimeoutError, URLError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Container verification failed: {type(exc).__name__}", file=sys.stderr)
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
