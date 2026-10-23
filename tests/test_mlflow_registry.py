"""MLflow Registry의 동일 학습 중복 등록·불변 참조 검증."""

import pytest

from sos_platform.mlflow_errors import MLflowDeliveryError
from sos_platform.mlflow_registry import (
    register_model_version, registered_model_name, verify_registered_version,
)


class FakeClient:
    def __init__(self):
        self.models = []
        self.versions = []
        self.created = 0

    def get(self, endpoint, params):
        if endpoint == "registered-models/search":
            return {"registered_models": self.models}
        if endpoint == "model-versions/search":
            return {"model_versions": self.versions}
        if endpoint == "model-versions/get":
            selected = [
                item for item in self.versions
                if item["name"] == params["name"] and item["version"] == params["version"]
            ]
            return {"model_version": selected[0]} if selected else {}
        raise AssertionError(endpoint)

    def post(self, endpoint, data):
        if endpoint == "registered-models/create":
            item = {"name": data["name"]}
            self.models.append(item)
            return {"registered_model": item}
        if endpoint == "model-versions/create":
            self.created += 1
            item = {**data, "version": str(self.created)}
            self.versions.append(item)
            return {"model_version": item}
        raise AssertionError(endpoint)


@pytest.fixture
def model():
    return {
        "job_id": "job-1", "owner_actor": "operator-one",
        "algorithm": "isolation_forest", "sha256": "a" * 64,
        "location": "s3://sos-data/results/job-1/attempt-1.model.joblib",
    }


def test_registry_register_and_retry_share_exact_version(model):
    client = FakeClient()
    first = register_model_version(client, model, "run-1")
    assert first == (registered_model_name(model), "1")
    assert register_model_version(client, model, "run-1") == first
    assert client.created == 1


def test_registry_pins_artifact_source_and_rejects_changed_version(model):
    client = FakeClient()
    name, version = register_model_version(client, model, "run-1")
    client.versions[0]["status"] = "READY"
    pinned = {**model, "registry_model": name, "registry_version": version}
    verify_registered_version(client, pinned)
    client.versions[0]["status"] = "PENDING_REGISTRATION"
    with pytest.raises(MLflowDeliveryError, match="MISMATCH"):
        verify_registered_version(client, pinned)
    client.versions[0]["status"] = "READY"
    with pytest.raises(MLflowDeliveryError, match="MISMATCH"):
        verify_registered_version(client, {**pinned, "sha256": "b" * 64})
    client.versions[0]["source"] = "s3://sos-data/results/other.model.joblib"
    with pytest.raises(MLflowDeliveryError, match="MISMATCH"):
        verify_registered_version(client, pinned)


def test_registry_rejects_mutated_artifact_same_job(model):
    client = FakeClient()
    register_model_version(client, model, "run-1")
    with pytest.raises(MLflowDeliveryError, match="MISMATCH"):
        register_model_version(client, {**model, "sha256": "b" * 64}, "run-1")


def test_registry_rejects_duplicate_versions(model):
    client = FakeClient()
    register_model_version(client, model, "run-1")
    client.versions.append({**client.versions[0], "version": "2"})
    with pytest.raises(MLflowDeliveryError, match="DUPLICATE"):
        register_model_version(client, model, "run-1")


def test_registry_separates_actor_model_names(model):
    other = {**model, "owner_actor": "operator-two"}
    assert registered_model_name(model) != registered_model_name(other)


@pytest.mark.parametrize("patch", [
    {"sha256": "invalid"},
    {"algorithm": "unknown"},
    {"owner_actor": None},
    {"location": "file:///tmp/bad.model"},
])
def test_registry_rejects_invalid_model(model, patch):
    with pytest.raises(MLflowDeliveryError):
        register_model_version(FakeClient(), {**model, **patch}, "run-1")
