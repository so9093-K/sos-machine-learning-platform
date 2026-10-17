"""Make 재시작·영속성 검증을 테스트 소유 인프라에서 수행한다."""

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from run_isolated_e2e import (
    _interrupted,
    clean_owned,
    create_owner,
    free_ports,
    isolated_environment,
)

ROOT = Path(__file__).resolve().parent.parent


def make(env: dict[str, str], command: str) -> None:
    """공개 Make 인터페이스를 실제로 실행한다."""
    subprocess.run(["make", command], cwd=ROOT, env=env, check=True)


def request_json(url: str, *, data: bytes | None = None, content_type: str = "") -> dict:
    """내부 테스트 API의 성공 응답만 JSON으로 읽는다."""
    headers = {"Content-Type": content_type} if content_type else {}
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def upload(url: str) -> dict:
    """결정적 CSV를 별도 DB/S3에 등록하고 HTTP 201을 확인한다."""
    boundary = "sos-test-" + secrets.token_hex(8)
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="lifecycle.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        "timestamp,value\n1,10\n2,20\n"
        f"\r\n--{boundary}--\r\n"
    ).encode()
    request = Request(
        url + "/api/datasets/files",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 201:
            raise RuntimeError("Dataset 등록 상태가 201이 아닙니다.")
        return json.load(response)


def verify(env: dict[str, str]) -> None:
    """동일 고유 리소스의 재시작·중단 후 데이터와 Snapshot을 대조한다."""
    url = f"http://127.0.0.1:{env['SOS_API_PORT']}"
    make(env, "up")
    make(env, "status")
    make(env, "doctor")
    make(env, "help")
    subprocess.run(["make"], cwd=ROOT, env=env, check=True)
    deep = subprocess.run(
        ["python3", "scripts/dev.py", "doctor", "--deep", "--format", "json"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if json.loads(deep.stdout)["status"] != "passed":
        raise AssertionError("Make deep doctor 검증 실패")
    dataset = upload(url)
    dataset_id = dataset["id"]
    table_name = f"ci_lifecycle_{secrets.token_hex(6)}"
    payload = json.dumps({"dataset_id": dataset_id, "table_name": table_name}).encode()
    job = request_json(url + "/api/iceberg/import", data=payload, content_type="application/json")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = request_json(url + "/api/jobs/" + job["id"])
        if state["status"] == "Succeeded":
            break
        if state["status"] in {"Failed", "Cancelled"}:
            raise RuntimeError("Iceberg 등록 Job이 성공하지 못했습니다.")
        time.sleep(0.25)
    else:
        raise TimeoutError("Iceberg 등록 Job 완료 시간을 초과했습니다.")

    for operation in ("restart", "down", "up"):
        make(env, operation)
        if operation == "down":
            continue
        rows = request_json(url + "/api/datasets")["items"]
        if not any(row["id"] == dataset_id for row in rows):
            raise AssertionError("재시작 후 원본 Dataset이 소실됐습니다.")

    preview = request_json(url + f"/api/datasets/{dataset_id}/preview")
    assert preview["returned_rows"] == 2
    rows = request_json(url + "/api/datasets")["items"]
    iceberg = next(row for row in rows if row["name"] == f"default.{table_name}")
    ice_preview = request_json(url + f"/api/datasets/{iceberg['id']}/preview")
    assert ice_preview["returned_rows"] >= 2
    snapshots = request_json(url + f"/api/iceberg/{table_name}/snapshots")
    assert len(snapshots) >= 1
    make(env, "logs")
    jobs = request_json(url + "/api/jobs")["items"]
    assert any(row["id"] == job["id"] and row["status"] == "Succeeded" for row in jobs)


def main() -> int:
    signal.signal(signal.SIGTERM, _interrupted)
    instance = "sos-e2e-" + secrets.token_hex(6)
    ports = free_ports()
    env = isolated_environment(os.environ, instance, ports)
    runtime = create_owner(instance, ports)
    status = 0
    try:
        verify(env)
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        AssertionError,
        subprocess.CalledProcessError,
        KeyboardInterrupt,
        InterruptedError,
    ):
        print(f"Make 영속성 검증 실패: {instance}", file=sys.stderr)
        status = 1
    finally:
        if clean_owned(instance, runtime, env):
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
