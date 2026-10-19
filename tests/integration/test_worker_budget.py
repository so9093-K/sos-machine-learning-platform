"""실행 시간 초과는 Job·Attempt에 안정적인 오류를 기록한다."""

import pytest

from sos_platform.execution_budget import ExecutionBudget
from sos_platform.worker import run_once

from .support import upload


def test_worker_wall_timeout_stops_job_and_records_error(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert queued.status_code == 202
    monkeypatch.setattr(
        ExecutionBudget,
        "from_env",
        classmethod(lambda cls: cls(max_wall_seconds=0.00001, max_rss_mib=2048)),
    )
    assert run_once(store)
    job = store.get_job(queued.json()["id"])
    assert job["status"] == "Failed"
    assert job["error_code"] == "SOS-JOB-TIMEOUT"
    assert job["failure_stage"] == "execution"
    assert job["failure_category"] == "timeout"
    assert job["attempts"][0]["error_code"] == "SOS-JOB-TIMEOUT"


def test_worker_staging_budget_rejects_oversize_attempt_and_keeps_commit_uncertain(
    workspace, monkeypatch
):
    """부모가 스테이징 초과를 감지하면 중단하되 외부 커밋 상태는 추측하지 않는다."""
    import sos_platform.worker as worker

    client, store = workspace
    dataset = upload(client)
    queued = client.post(
        "/api/iceberg/import",
        json={"dataset_id": dataset["id"], "table_name": "stage_budget"},
    )
    assert queued.status_code == 202
    monkeypatch.setattr(
        ExecutionBudget,
        "from_env",
        classmethod(lambda cls: cls(max_wall_seconds=1800, max_rss_mib=2048, max_stage_mib=1)),
    )
    monkeypatch.setattr(worker, "iceberg_staging_bytes", lambda *_args: 2 * 1024 * 1024)
    assert run_once(store)
    job = store.get_job(queued.json()["id"])
    assert job["status"] == "Failed"
    assert job["error_code"] == "SOS-ICEBERG-UNCERTAIN"
    assert job["failure_category"] == "storage"
    assert not list((store.home / "cache" / "iceberg-staging").glob("attempt-*"))


@pytest.mark.pr
def test_required_cgroup_rejects_job_before_claim_when_not_delegated(workspace, monkeypatch):
    """물리 격리가 필수인데 cgroup 위임이 없으면 실행을 받아서는 안 된다."""
    import pytest

    from sos_platform.errors import RuntimeConfigurationError

    client, store = workspace
    dataset = upload(client)
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert queued.status_code == 202
    monkeypatch.setenv("SOS_WORKER_CGROUP_MODE", "required")
    # /sys/fs/cgroup 아래에 위임된 별도 디렉터리가 아니므로 절대 실행하지 않는다.
    monkeypatch.setenv("SOS_WORKER_CGROUP_PARENT", str(store.home))
    with pytest.raises(RuntimeConfigurationError, match="cgroup v2 mount"):
        run_once(store)
    saved = store.get_job(queued.json()["id"])
    assert saved["status"] == "Queued"
    assert saved["attempts"] == []
