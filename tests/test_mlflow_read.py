"""Actor의 MLflow 검색 결과에서 타인 Job 및 신뢰 불일치를 제거한다."""

from types import SimpleNamespace

from sos_platform.mlflow_experiments import ensure_experiment, experiment_name
from sos_platform.mlflow_read import read_experiments, read_model_versions, read_runs
from sos_platform.mlflow_registry import registered_model_name


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


def test_model_search_rechecks_owner_and_bound_artifact_digest():
    client = ResearchClient()
    name = registered_model_name({"owner_actor": "alice", "algorithm": "isolation_forest"})
    client.versions = [
        {"name": name, "version": "1", "status": "READY",
         "source": "s3://sos-data/results/job/attempt.model.joblib",
         "tags": [
             {"key": "sos.job_id", "value": "own"},
             {"key": "sos.sha256", "value": "a" * 64},
         ]},
        {"name": name, "version": "2", "status": "READY",
         "source": "s3://sos-data/results/foreign/attempt.model.joblib",
         "tags": [
             {"key": "sos.job_id", "value": "foreign"},
             {"key": "sos.sha256", "value": "b" * 64},
         ]},
    ]
    jobs = {
        "own": {"owner_actor": "alice", "result": {"model_revision_id": "model-a"}},
        "foreign": {"owner_actor": "bob", "result": {"model_revision_id": "model-b"}},
    }
    refs = {"model-a": {"registry_model": name, "registry_version": "1",
                        "location": "s3://sos-data/results/job/attempt.model.joblib",
                        "sha256": "a" * 64, "owner_actor": "alice"}}
    store = SimpleNamespace(get_job=jobs.get, get_model_revision=refs.get)
    result = read_model_versions(client, store, "alice")
    assert len(result["items"]) == 1
    assert result["items"][0]["model_id"] == "model-a"
    refs["model-a"]["sha256"] = "c" * 64
    assert read_model_versions(client, store, "alice")["items"] == []
    refs["model-a"]["sha256"] = "a" * 64
    refs["model-a"]["location"] = "s3://sos-data/results/different.model.joblib"
    assert read_model_versions(client, store, "alice")["items"] == []


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


def test_run_search_with_archived_tag_does_not_crash():
    client = ResearchClient()
    ensure_experiment(client, "alice")
    client.runs = [
        {"info": {"run_id": "archived-run"}, "data": {
            "tags": [
                {"key": "sos.job_id", "value": "j1"},
                {"key": "sos.archived", "value": "true"},
            ],
        }}
    ]
    store = SimpleNamespace(
        get_job=lambda job_id: {"owner_actor": "alice", "kind": "ml_train"}
        if job_id == "j1" else None,
    )
    assert [x["id"] for x in read_runs(client, store, "alice")["items"]] == ["archived-run"]


def test_archived_version_is_hidden_from_use_but_visible_for_management():
    client = ResearchClient()
    name = registered_model_name({"owner_actor": "alice", "algorithm": "isolation_forest"})
    client.versions = [{
        "name": name, "version": "4", "status": "READY",
        "source": "s3://sos-data/results/job/attempt.model.joblib",
        "tags": [
            {"key": "sos.job_id", "value": "own"},
            {"key": "sos.sha256", "value": "a" * 64},
            {"key": "sos.archived", "value": "true"},
        ],
    }]
    model = {
        "registry_model": name, "registry_version": "4",
        "location": client.versions[0]["source"], "sha256": "a" * 64,
        "owner_actor": "alice",
    }
    store = SimpleNamespace(
        get_job=lambda _: {"owner_actor": "alice", "result": {"model_revision_id": "model-a"}},
        get_model_revision=lambda _: model,
    )
    assert read_model_versions(client, store, "alice")["items"] == []
    assert read_model_versions(client, store, "alice", include_archived=True)["items"][0]["archived"]

    client.versions[0]["tags"] = [tag for tag in client.versions[0]["tags"]
                                   if tag["key"] != "sos.archived"]
    client.group_tags = [{"key": "sos.archived", "value": "true"}]
    assert read_model_versions(client, store, "alice")["items"] == []
    assert read_model_versions(client, store, "alice", include_archived=True)["items"]
