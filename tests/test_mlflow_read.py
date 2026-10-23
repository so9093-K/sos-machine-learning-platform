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

    def get(self, endpoint, params):
        if endpoint == "model-versions/search":
            return {"model_versions": self.versions}
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
    assert read_experiments(client, "bob") == []
    assert read_experiments(client, "alice") == [
        {"id": "23", "name": "내 ML 실험"},
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
    assert len(result) == 1
    assert result[0]["id"] == "r1"
    assert result[0]["metrics"]["sos.score_mean"] == 0.9


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
    assert len(result) == 1
    assert result[0]["model_id"] == "model-a"
    refs["model-a"]["sha256"] = "c" * 64
    assert read_model_versions(client, store, "alice") == []
    refs["model-a"]["sha256"] = "a" * 64
    refs["model-a"]["location"] = "s3://sos-data/results/different.model.joblib"
    assert read_model_versions(client, store, "alice") == []
