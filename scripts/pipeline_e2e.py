"""격리된 Compose 환경에서 등록형 IF Pipeline의 실제 사용자 E2E를 실행한다.

이 스크립트는 명시적인 검증 명령에서만 실행한다. Runtime 시작·배포 과정에 결합하지 않는다.
"""
import json
import os
import sys
import time
from pathlib import Path
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import polars as pl

from sos_platform.pipeline_reference import FEATURES

URL = os.environ.get("SOS_E2E_URL", "http://127.0.0.1:9602")
TIMEOUT = 240


def request(token: str, method: str, path: str, *, data: bytes | None = None,
            content_type: str | None = None, key: str | None = None,
            expected: int = 200) -> dict:
    headers = {"Authorization": "Bearer " + token}
    if content_type:
        headers["Content-Type"] = content_type
    if key:
        headers["Idempotency-Key"] = key
    req = Request(URL + path, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=60) as response:
            actual = response.status
            payload = response.read()
    except HTTPError as exc:
        actual = exc.code
        payload = exc.read()
    if actual != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {actual}; {payload[:500]!r}")
    return json.loads(payload) if payload else {}


def make_file(user_offset: int) -> bytes:
    rows = 120
    start = datetime(2026, 2, 1, tzinfo=UTC)
    data = {
        "ts": [(start + timedelta(minutes=i)).isoformat() for i in range(rows)]
    }
    for col, name in enumerate(FEATURES):
        data[name] = [
            round((i % 19) * 0.1 + ((i * (col % 7 + 2)) % 11) * 0.08
                  + col * 0.005 + user_offset * 0.02, 5)
            for i in range(rows)
        ]
    buf = BytesIO()
    pl.DataFrame(data).write_parquet(buf)
    return buf.getvalue()


def upload(token: str, offset: int) -> dict:
    boundary = "sos-e2e-" + uuid4().hex
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="activity-e2e.parquet"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body = prefix + make_file(offset) + f"\r\n--{boundary}--\r\n".encode()
    return request(
        token, "POST", "/api/datasets/files", data=body,
        content_type="multipart/form-data; boundary=" + boundary, expected=201,
    )


def start(token: str, dataset_id: str) -> dict:
    inspected = request(
        token, "GET",
        "/api/pipelines/activity-anomaly-if/check?" + urlencode({"dataset_id": dataset_id}),
    )
    assert inspected["ready"] and inspected["plan"] is None, inspected
    key = "integration-" + uuid4().hex
    body = json.dumps({"dataset_id": dataset_id}).encode()
    first = request(
        token, "POST", "/api/pipelines/activity-anomaly-if/runs",
        data=body, content_type="application/json", key=key, expected=202,
    )
    duplicate = request(
        token, "POST", "/api/pipelines/activity-anomaly-if/runs",
        data=body, content_type="application/json", key=key, expected=202,
    )
    assert duplicate["id"] == first["id"], (first, duplicate)
    return first


def complete(token: str, run_id: str) -> dict:
    until = time.monotonic() + TIMEOUT
    while time.monotonic() < until:
        run = request(token, "GET", f"/api/pipeline-runs/{run_id}")
        if run["status"] in ("Succeeded", "Failed", "Cancelled"):
            assert run["status"] == "Succeeded", run
            assert len(run["stages"]) == 6, run
            assert all(entry["job"]["status"] == "Succeeded" for entry in run["stages"])
            assert run["input_plan"] is not None
            assert min(run["input_plan"]["partition_sizes"].values()) > 0
            page = request(token, "GET", f"/api/pipeline-runs/{run_id}/candidates?limit=5")
            assert "threshold" in page and isinstance(page["items"], list), page
            assert len(page["items"]) <= 5
            return run
        time.sleep(2)
    raise TimeoutError(f"Pipeline Run {run_id} did not finish within {TIMEOUT}s")


def main() -> None:
    token_a = os.environ["SOS_E2E_TOKEN_A"]
    token_b = os.environ["SOS_E2E_TOKEN_B"]
    catalog = request(token_a, "GET", "/api/pipelines")
    assert len(catalog) == 1 and catalog[0]["id"] == "activity-anomaly-if", catalog
    dataset_a = upload(token_a, 1)
    request(token_b, "GET", f"/api/datasets/{dataset_a['id']}", expected=404)
    run_a = start(token_a, dataset_a["id"])
    request(token_b, "GET", f"/api/pipeline-runs/{run_a['id']}", expected=404)
    request(
        token_b, "GET", "/api/pipelines/activity-anomaly-if/check?"
        + urlencode({"dataset_id": dataset_a["id"]}), expected=404,
    )
    dataset_b = upload(token_b, 3)
    run_b = start(token_b, dataset_b["id"])
    assert run_a["id"] != run_b["id"]
    finished_a = complete(token_a, run_a["id"])
    finished_b = complete(token_b, run_b["id"])
    assert finished_a["dataset_version"] != finished_b["dataset_version"]
    assert finished_a["stages"][1]["job"]["result"]["model_revision_id"] != (
        finished_b["stages"][1]["job"]["result"]["model_revision_id"]
    )
    for token, own, other in [(token_a, run_a, run_b), (token_b, run_b, run_a)]:
        history = request(token, "GET", "/api/pipeline-runs")
        assert any(item["id"] == own["id"] for item in history)
        assert all(item["id"] != other["id"] for item in history)
        request(token, "GET", f"/api/pipeline-runs/{other['id']}/candidates", expected=404)
    print("Registered Pipeline E2E: two isolated Dataset -> Training -> Scoring -> Policy -> Evaluation runs succeeded")


def _cancel_record() -> Path:
    return Path(os.environ["RUNNER_TEMP"]) / "sos-pipeline-cancelled-run"


def queued_cancel_submission() -> None:
    """Worker가 정지된 동안 제출과 취소 요청을 별개로 확정한다."""
    token = os.environ["SOS_E2E_TOKEN_A"]
    dataset = upload(token, 5)
    run = start(token, dataset["id"])
    assert run["status"] == "Queued", run
    cancelled = request(token, "POST", f"/api/pipeline-runs/{run['id']}/cancel")
    assert cancelled["cancel_requested"] and cancelled["status"] == "Queued", cancelled
    _cancel_record().write_text(run["id"], encoding="ascii")


def check_cancelled_after_restart() -> None:
    """Worker 재시작 후 이미 취소된 Run이 ML Job을 만들지 않는지 검증한다."""
    token = os.environ["SOS_E2E_TOKEN_A"]
    run_id = _cancel_record().read_text(encoding="ascii")
    until = time.monotonic() + 60
    while time.monotonic() < until:
        run = request(token, "GET", f"/api/pipeline-runs/{run_id}")
        if run["status"] == "Cancelled":
            assert run["stages"] == [], run
            print("Pipeline queued cancellation remained effective across worker restart")
            return
        time.sleep(1)
    raise TimeoutError("Cancelled Pipeline Run was not finalized after Worker restart")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode == "full":
        main()
    elif mode == "queue-cancel":
        queued_cancel_submission()
    elif mode == "check-cancelled":
        check_cancelled_after_restart()
    else:
        raise SystemExit("Unknown E2E scenario")
