"""MLflow 외부 전송 실패 시 SOS 학습 정본의 성공·복구를 검증한다."""

from uuid import uuid4

from sqlalchemy import text

from sos_platform.mlflow_config import MLflowSettings
from sos_platform.mlflow_tracking import MLflowClient, MLflowDeliveryError, run_tracking_once
from sos_platform.worker import run_once

from .support import upload


class StubMLflow(MLflowClient):
    def __init__(self):
        super().__init__(MLflowSettings("https://tracking.example.org", "7", None))
        self.calls = []
        self.fail_log = True

    def post(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        if endpoint == "runs/search":
            return {"runs": []}
        if endpoint == "runs/create":
            return {"run": {"info": {"run_id": "run-1"}}}
        if endpoint == "runs/log-batch":
            if self.fail_log:
                raise MLflowDeliveryError("SOS-MLFLOW-UNAVAILABLE")
            return {}
        if endpoint == "runs/update":
            return {}
        raise AssertionError(endpoint)


def test_tracking_export_failure_retries_without_mutating_successful_job(workspace, monkeypatch):
    client, store = workspace
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "https://tracking.example.org")
    monkeypatch.setenv("SOS_MLFLOW_EXPERIMENT_ID", "7")
    dataset = upload(client)
    job = client.post(
        "/api/ml/train/jobs",
        json={"dataset_id": dataset["id"], "features": ["temperature"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert job.status_code == 202
    assert run_once(store) is True
    job_id = job.json()["id"]
    assert client.get(f"/api/ml/train/jobs/{job_id}").json()["status"] == "Succeeded"
    assert store.tracking_status(job_id)["state"] == "Pending"
    assert client.get(f"/api/ml/tracking/jobs/{job_id}").json()["state"] == "Pending"
    transport = StubMLflow()
    assert run_tracking_once(store, transport) is True
    failed = store.tracking_status(job_id)
    assert failed["state"] == "Retry"
    assert failed["attempts"] == 1
    assert failed["run_id"] == "run-1"
    assert failed["last_error_code"] == "SOS-MLFLOW-UNAVAILABLE"
    assert client.get(f"/api/ml/tracking/jobs/{job_id}").json()["state"] == "Retry"
    assert client.get(f"/api/ml/train/jobs/{job_id}").json()["status"] == "Succeeded"

    with store.connection() as db:
        db.execute(
            text("UPDATE mlflow_exports SET retry_after=:when WHERE job_id=:job"),
            {"when": "2000-01-01T00:00:00+00:00", "job": job_id},
        )
    transport.fail_log = False
    assert run_tracking_once(store, transport) is True
    done = store.tracking_status(job_id)
    assert done["state"] == "Succeeded"
    assert done["attempts"] == 2
    assert done["run_id"] == "run-1"
    status_response = client.get(f"/api/ml/tracking/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["run_id"] == "run-1"
    assert status_response.json()["state"] == "Succeeded"
    assert [name for name, _ in transport.calls].count("runs/create") == 1
    assert run_tracking_once(store, transport) is False
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM mlflow_exports")).scalar_one() == 1
        assert (
            db.execute(
                text("SELECT COUNT(*) FROM audit_events WHERE action='mlflow.export'")
            ).scalar_one()
            == 2
        )


def test_tracking_inflight_recovery_keeps_external_run_id(workspace, monkeypatch):
    client, store = workspace
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "https://tracking.example.org")
    dataset = upload(client)
    req = client.post(
        "/api/ml/train/jobs",
        json={"dataset_id": dataset["id"], "features": ["temperature"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert run_once(store)
    claimed = store.claim_tracking_export()
    assert claimed["job_id"] == req.json()["id"]
    store.save_tracking_run_id(claimed["job_id"], "recovered-run")
    assert store.recover_tracking_exports() == 1
    recovered = store.claim_tracking_export()
    assert recovered["run_id"] == "recovered-run"
    assert recovered["attempts"] == 2
