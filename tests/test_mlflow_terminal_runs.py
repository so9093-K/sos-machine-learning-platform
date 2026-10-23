"""실패한 연구 Job도 MLflow에서 이유를 추적할 수 있고 모델 버전은 생성하지 않는다."""

from sos_platform.mlflow_tracking import send_to_mlflow


class Client:
    def __init__(self):
        self.requests = []

    def post(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        if endpoint == "experiments/search":
            return {"experiments": []}
        if endpoint == "experiments/create":
            return {"experiment_id": "1"}
        if endpoint == "runs/search":
            return {"runs": []}
        if endpoint == "runs/create":
            return {"run": {"info": {"run_id": "run-1"}}}
        return {}


def test_failed_training_is_recorded_without_fake_success_metrics():
    client = Client()
    job = {
        "id": "job-1", "kind": "ml_train", "status": "Failed",
        "dataset_id": "dataset-1", "owner_actor": "alice",
        "spec": {"params": {"n_estimators": 10}, "experiment_title": "ablation"},
        "result": None,
    }
    remembered = []
    assert send_to_mlflow(
        client, job, known_run_id=None, remember_run=remembered.append
    ) == "run-1"
    assert remembered == ["run-1"]
    updates = [payload for name, payload in client.requests if name == "runs/update"]
    assert updates == [{"run_id": "run-1", "status": "FAILED"}]
    metrics = [payload for name, payload in client.requests if name == "runs/log-batch"]
    assert metrics[0]["metrics"] == []


def test_cancelled_job_is_not_reported_as_finished_success():
    client = Client()
    job = {
        "id": "job-2", "kind": "ml_train", "status": "Cancelled",
        "dataset_id": "dataset-1", "owner_actor": "alice",
        "spec": {"params": {}}, "result": None,
    }
    send_to_mlflow(client, job, known_run_id=None, remember_run=lambda _: None)
    assert [payload["status"] for name, payload in client.requests if name == "runs/update"] == ["KILLED"]
