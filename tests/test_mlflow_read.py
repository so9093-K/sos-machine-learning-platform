"""Actor의 MLflow 검색 결과에서 타인 Job 및 신뢰 불일치를 제거한다."""

from types import SimpleNamespace

from sos_platform.mlflow_experiments import ensure_experiment, experiment_name
from sos_platform.mlflow_read import read_experiments, read_runs


class ResearchClient:
    def __init__(self):
        self.experiment = None
        self.versions = []
        self.runs = []
        self.group_tags = []

    def get(self, endpoint, params):
        if endpoint == "registered-models/get":
            return {"registered_model": {"name": params["name"], "tags": self.group_tags}}
        if endpoint == "model-versions/search":
            return {"model_versions": self.versions}
        if endpoint == "experiments/get":
            if self.experiment and self.experiment["experiment_id"] == params["experiment_id"]:
                return {"experiment": self.experiment}
            return {}

        raise AssertionError(endpoint)

    def post(self, endpoint, data):
        if endpoint == "experiments/search":
            return {"experiments": [self.experiment] if self.experiment else []}
        if endpoint == "experiments/create":
            self.experiment = {
                "name": data["name"], "experiment_id": "23",
            }
            return {"experiment_id": "23"}
        if endpoint == "runs/search":
            return {"runs": self.runs}
        raise AssertionError(endpoint)


def test_owner_experiment_uses_stable_name_and_reuses_existing():
    client = ResearchClient()
    assert ensure_experiment(client, "alice") == "23"
    assert ensure_experiment(client, "alice") == "23"
    assert read_experiments(client, "bob")["items"] == []
    assert read_experiments(client, "alice")["items"] == [
        {"id": "23", "name": "기본 실험"},
    ]
    assert experiment_name("alice") != experiment_name("bob")


def test_run_search_rechecks_sos_job_owner():
    client = ResearchClient()
    ensure_experiment(client, "alice")
    client.runs = [
        {"info": {"run_id": "r1", "status": "FINISHED"}, "data": {
            "tags": [{"key": "sos.job_id", "value": "own"}],
            "params": [{"key": "sos.n_estimators", "value": "100"}],
            "metrics": [{"key": "sos.score_mean", "value": 0.9}],
        }},
        {"info": {"run_id": "r2"}, "data": {
            "tags": [{"key": "sos.job_id", "value": "foreign"}],
        }},
    ]
    jobs = {
        "own": {"id": "own", "owner_actor": "alice", "kind": "ml_train"},
        "foreign": {"id": "foreign", "owner_actor": "bob", "kind": "ml_train"},
    }
    store = SimpleNamespace(get_job=jobs.get)
    result = read_runs(client, store, "alice")
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "r1"
    assert result["items"][0]["metrics"]["sos.score_mean"] == 0.9
    assert len(read_runs(client, store, "alice", experiment_id="23")["items"]) == 1


def test_experiment_titles_are_separate_without_sos_management_table():
    assert experiment_name("alice", "baseline") != experiment_name("alice", "ablation")
    assert experiment_name("alice", "baseline") != experiment_name("bob", "baseline")


def test_run_search_rejects_other_actor_experiment():
    client = ResearchClient()
    client.experiment = {"name": experiment_name("bob"), "experiment_id": "23"}
    store = SimpleNamespace(get_job=lambda _: None)
    from sos_platform.mlflow_errors import MLflowDeliveryError
    import pytest

    with pytest.raises(MLflowDeliveryError, match="EXPERIMENT-NOT-FOUND"):
        read_runs(client, store, "alice", experiment_id="23")


def test_mlflow_empty_page_token_is_end_of_results():
    from sos_platform.mlflow_read import _page_token

    assert _page_token({}) is None
    assert _page_token({"next_page_token": ""}) is None
    assert _page_token({"next_page_token": "opaque-cursor"}) == "opaque-cursor"


def test_missing_experiment_returns_not_found_instead_of_management_outage():
    from sos_platform.mlflow_errors import MLflowDeliveryError
    import pytest

    class NotFoundClient(ResearchClient):
        def get(self, endpoint, params):
            if endpoint == "experiments/get":
                raise MLflowDeliveryError("SOS-MLFLOW-HTTP-404-experiments_get")
            return super().get(endpoint, params)

    with pytest.raises(MLflowDeliveryError, match="EXPERIMENT-NOT-FOUND"):
        read_runs(NotFoundClient(), SimpleNamespace(get_job=lambda _: None), "alice",
                  experiment_id="999")


