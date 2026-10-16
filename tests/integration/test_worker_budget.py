"""실행 시간 초과는 Job·Attempt에 안정적인 오류를 기록한다."""

from sos_platform.execution_budget import ExecutionBudget
from sos_platform.worker import run_once

from .support import upload


def test_worker_wall_timeout_stops_job_and_records_error(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    queued = client.post(
        "/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}
    )
    assert queued.status_code == 202
    monkeypatch.setattr(
        ExecutionBudget, "from_env",
        classmethod(lambda cls: cls(max_wall_seconds=0.00001, max_rss_mib=2048)),
    )
    assert run_once(store)
    job = store.get_job(queued.json()["id"])
    assert job["status"] == "Failed"
    assert job["error_code"] == "SOS-JOB-TIMEOUT"
    assert job["failure_stage"] == "execution"
    assert job["failure_category"] == "timeout"
    assert job["attempts"][0]["error_code"] == "SOS-JOB-TIMEOUT"
