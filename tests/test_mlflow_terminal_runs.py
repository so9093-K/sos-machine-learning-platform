"""실패한 연구 Job도 MLflow에서 이유를 추적할 수 있고 모델 버전은 생성하지 않는다."""

from sos_platform.mlflow_tracking import _job_experiment_title, send_to_mlflow


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
        "spec": {
            "params": {"n_estimators": 10}, "experiment_title": "ablation",
            "dataset_version": "sha256-dataset-v1",
        },
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
    logged_params = {item["key"]: item["value"] for item in metrics[0]["params"]}
    assert logged_params["sos.dataset_version"] == "sha256-dataset-v1"
    assert logged_params["sos.job_kind"] == "ml_train"


def test_cancelled_job_is_not_reported_as_finished_success():
    client = Client()
    job = {
        "id": "job-2", "kind": "ml_train", "status": "Cancelled",
        "dataset_id": "dataset-1", "owner_actor": "alice",
        "spec": {"params": {}}, "result": None,
    }
    send_to_mlflow(client, job, known_run_id=None, remember_run=lambda _: None)
    assert [payload["status"] for name, payload in client.requests if name == "runs/update"] == ["KILLED"]


def test_linked_ml_jobs_share_the_source_training_experiment():
    """Run 비교에서 연구가 실행 단계마다 기본 Experiment로 분산되지 않는다."""
    from types import SimpleNamespace

    training = {
        "kind": "ml_train", "owner_actor": "alice",
        "spec": {"experiment_title": "feature-ablation"},
    }
    store = SimpleNamespace(
        get_model_revision=lambda model_id: (
            {"job_id": "train-1"} if model_id == "model-1" else None
        ),
        get_job=lambda job_id: training if job_id == "train-1" else None,
        get_scoring_result=lambda result_id: (
            {"model_revision_id": "model-1"} if result_id == "score-1" else None
        ),
    )
    for kind, spec in [
        ("ml_score", {"model_revision_id": "model-1"}),
        ("ml_policy", {"scoring_result_id": "score-1"}),
        ("ml_evaluate", {"scoring_result_id": "score-1"}),
        ("ml_explain", {"scoring_result_id": "score-1"}),
    ]:
        assert _job_experiment_title(
            store, {"kind": kind, "owner_actor": "alice", "spec": spec}
        ) == "feature-ablation"
        assert _job_experiment_title(
            store, {"kind": kind, "owner_actor": "bob", "spec": spec}
        ) == "기본 실험"
