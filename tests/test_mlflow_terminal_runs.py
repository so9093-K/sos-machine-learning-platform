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


def test_mlflow_run_uses_recorded_attempt_utc_times_and_does_not_use_retry_clock():
    """Worker 재전송 시점이 아니라 SOS가 확정한 Attempt 시각을 전송한다."""
    client = Client()
    job = {
        "id": "job-timed", "kind": "ml_train", "status": "Succeeded",
        "dataset_id": "dataset-1", "owner_actor": "alice",
        "created_at": "2026-10-26T06:00:00+00:00",
        "updated_at": "2026-10-26T06:04:00+00:00",
        "attempts": [{
            "started_at": "2026-10-26T06:01:00Z",
            "finished_at": "2026-10-26T06:03:30+00:00",
        }],
        "spec": {"params": {}},
        "result": {"training_score_summary": {"score_count": 144}},
    }
    started_ms = 1792994460000
    finished_ms = 1792994610000

    send_to_mlflow(client, job, known_run_id=None, remember_run=lambda _: None)
    created = next(payload for endpoint, payload in client.requests if endpoint == "runs/create")
    updated = next(payload for endpoint, payload in client.requests if endpoint == "runs/update")
    metrics = next(payload for endpoint, payload in client.requests if endpoint == "runs/log-batch")["metrics"]
    assert created["start_time"] == started_ms
    assert updated["end_time"] == finished_ms
    assert metrics[0]["timestamp"] == finished_ms

    # Outbox Retry는 기존 Run을 재사용하며 시작 시각을 임의 수정하지 않는다.
    client.requests.clear()
    send_to_mlflow(client, job, known_run_id="run-1", remember_run=lambda _: None)
    assert all(endpoint != "runs/create" for endpoint, _ in client.requests)
    assert next(payload for endpoint, payload in client.requests if endpoint == "runs/update")[
        "end_time"
    ] == finished_ms


def test_mlflow_run_uses_job_lifecycle_for_no_attempt_and_omits_unknown_timestamps():
    """실제 Attempt가 없는 Job은 접수 시각을 사용하고, 시각을 추정해 생성하지 않는다."""
    from sos_platform.mlflow_tracking import _job_event_times, _unix_millis

    assert _unix_millis("2026-10-26T06:00:00") is None  # 타임존 미정
    assert _unix_millis("not-a-date") is None
    assert _unix_millis(None) is None
    job = {
        "id": "job-no-attempt", "kind": "ml_train", "status": "Cancelled",
        "dataset_id": "dataset-1", "owner_actor": "alice",
        "created_at": "2026-10-26T06:00:00+00:00",
        "updated_at": "2026-10-26T06:00:01+00:00",
        "spec": {"params": {}}, "result": None,
    }
    client = Client()
    send_to_mlflow(client, job, known_run_id=None, remember_run=lambda _: None)
    assert _job_event_times(job)[0] is not None
    assert next(body for endpoint, body in client.requests if endpoint == "runs/create")["start_time"]
    assert next(body for endpoint, body in client.requests if endpoint == "runs/update")["end_time"]

    client.requests.clear()
    job.pop("created_at")
    job.pop("updated_at")
    send_to_mlflow(client, job, known_run_id=None, remember_run=lambda _: None)
    assert "start_time" not in next(body for endpoint, body in client.requests if endpoint == "runs/create")
    assert "end_time" not in next(body for endpoint, body in client.requests if endpoint == "runs/update")
