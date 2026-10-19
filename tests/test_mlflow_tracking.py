"""선택적 MLflow REST 추적의 민감값·재시도·중복 Run 복구 계약."""

import json

import pytest

from sos_platform.errors import RuntimeConfigurationError
from sos_platform.mlflow_config import MLflowSettings
from sos_platform.mlflow_tracking import MLflowDeliveryError, _numeric_metrics, send_to_mlflow


def _job():
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "kind": "ml_train",
        "status": "Succeeded",
        "dataset_id": "00000000-0000-4000-8000-000000000002",
        "spec": {"params": {"n_estimators": 20, "bootstrap": False}},
        "result": {
            "training_score_summary": {
                "score_mean": 0.35,
                "precision": None,
                "invalid": float("nan"),
                "score_count": 5,
            },
        },
    }


class FakeClient:
    def __init__(self):
        self.settings = MLflowSettings("https://tracking.example.org", "17", None)
        self.calls = []
        self.found = []
        self.fail_log = False

    def post(self, path, payload):
        self.calls.append((path, payload))
        if path == "runs/search":
            return {"runs": self.found}
        if path == "runs/create":
            return {"run": {"info": {"run_id": "known-mlflow-run"}}}
        if path == "runs/log-batch":
            if self.fail_log:
                raise MLflowDeliveryError("SOS-MLFLOW-UNAVAILABLE")
            return {}
        if path == "runs/update":
            return {"run_info": {"status": "FINISHED"}}
        raise AssertionError(path)


def test_export_only_explicit_training_params_and_finite_metrics():
    client = FakeClient()
    remembered = []
    assert send_to_mlflow(client, _job(), known_run_id=None, remember_run=remembered.append)
    assert remembered == ["known-mlflow-run"]
    batch = next(payload for name, payload in client.calls if name == "runs/log-batch")
    assert batch["run_id"] == "known-mlflow-run"
    assert [p["key"] for p in batch["params"]] == ["sos.bootstrap", "sos.n_estimators"]
    assert {m["key"] for m in batch["metrics"]} == {"sos.score_mean", "sos.score_count"}
    wire = json.dumps(client.calls)
    assert "training_score_summary" not in wire
    assert "Bearer " not in wire


def test_create_timeout_retry_search_recovers_same_run():
    client = FakeClient()
    client.fail_log = True
    saved = []
    with pytest.raises(MLflowDeliveryError):
        send_to_mlflow(client, _job(), known_run_id=None, remember_run=saved.append)
    assert saved == ["known-mlflow-run"]
    client.fail_log = False
    assert (
        send_to_mlflow(client, _job(), known_run_id=saved[0], remember_run=saved.append)
        == "known-mlflow-run"
    )
    assert [name for name, _ in client.calls].count("runs/create") == 1


def test_unknown_run_recovered_by_job_tag_without_creating_duplicate():
    client = FakeClient()
    client.found = [{"info": {"run_id": "from-search"}}]
    remembered = []
    assert (
        send_to_mlflow(client, _job(), known_run_id=None, remember_run=remembered.append)
        == "from-search"
    )
    assert remembered == ["from-search"]
    assert "runs/create" not in [name for name, _ in client.calls]
    client.found.append({"info": {"run_id": "duplicate"}})
    with pytest.raises(MLflowDeliveryError, match="DUPLICATE"):
        send_to_mlflow(client, _job(), known_run_id=None, remember_run=lambda _: None)


def test_configuration_is_explicit_and_refuses_unsafe_urls(monkeypatch):
    monkeypatch.delenv("SOS_MLFLOW_TRACKING_URI", raising=False)
    assert MLflowSettings.from_env() is None
    monkeypatch.setenv("SOS_MLFLOW_EXPERIMENT_ID", "5")
    for endpoint in (
        "http://mlflow.example.org:5000",
        "file:///tmp/mlruns",
        "https://user:pass@tracking.example.org",
        "https://tracking.example.org/custom/path",
        "https://tracking.example.org?api_key=secret",
    ):
        monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", endpoint)
        with pytest.raises(RuntimeConfigurationError):
            MLflowSettings.from_env()
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    assert MLflowSettings.from_env().experiment_id == "5"
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "https://mlflow.example.org")
    assert MLflowSettings.from_env().endpoint == "https://mlflow.example.org"


def test_metrics_do_not_export_labels_or_unbounded_payload():
    job = _job()
    job["kind"] = "ml_explain"
    job["result"] = {"feature_sensitivities": [{"actual_value": 1000}]}
    assert _numeric_metrics(job) == []


def test_configuration_rejects_malformed_url_without_leaking_it(monkeypatch):
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "https://[invalid")
    monkeypatch.setenv("SOS_MLFLOW_EXPERIMENT_ID", "5")
    with pytest.raises(RuntimeConfigurationError, match="Invalid MLflow Tracking endpoint"):
        MLflowSettings.from_env()
