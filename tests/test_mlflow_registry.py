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
        if endpoint == "registered-models/get":
            selected = next((item for item in self.models if item["name"] == params["name"]), None)
            return {"registered_model": selected}
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


def test_archived_model_group_rejects_new_version(model):
    client = FakeClient()
    register_model_version(client, model, "run-1")
    client.models[0]["tags"] = [{"key": "sos.archived", "value": "true"}]
    with pytest.raises(MLflowDeliveryError, match="ARCHIVED"):
        register_model_version(client, {**model, "job_id": "job-2"}, "run-2")


def test_fresh_registry_model_is_created_without_getting_missing_name(model):
    """실제 MLflow는 존재하지 않는 registered-models/get에 HTTP 404를 응답한다."""
    class StrictClient(FakeClient):
        def get(self, endpoint, params):
            if endpoint == "registered-models/get" and not any(
                item["name"] == params["name"] for item in self.models
            ):
                raise MLflowDeliveryError("SOS-MLFLOW-HTTP-404-registered-models_get")
            return super().get(endpoint, params)

    client = StrictClient()
    name, version = register_model_version(client, model, "run-1")
    assert (name, version) == (registered_model_name(model), "1")
    assert len(client.models) == 1
    # 기존 Run을 다시 보내도 새 모델·버전이 만들어지지 않는다.
    assert register_model_version(client, model, "run-1") == (name, version)
    assert client.created == 1


def test_existing_registry_group_is_checked_for_archive_and_reused(model):
    client = FakeClient()
    name = registered_model_name(model)
    client.models.append({"name": name})
    assert register_model_version(client, model, "run-1") == (name, "1")
    assert len(client.models) == 1


def test_registry_does_not_recreate_missing_group_with_existing_version(model):
    client = FakeClient()
    name = registered_model_name(model)
    client.versions.append({
        "name": name, "version": "1", "source": model["location"],
        "run_id": "run-1", "tags": [
            {"key": "sos.job_id", "value": model["job_id"]},
            {"key": "sos.sha256", "value": model["sha256"]},
        ],
    })
    with pytest.raises(MLflowDeliveryError, match="MODEL-MISMATCH"):
        register_model_version(client, model, "run-1")
    assert not client.models



@pytest.mark.parametrize("bad_result", [
    {"registered_models": ""},
    {"registered_models": {}},
    {"registered_models": [{"unexpected": "schema"}]},
    {"registered_models": [{"name": "another-model"}]},
    {"registered_models": [{"name": "sos-if-not-ours"}]},
    {"registered_models": [], "next_page_token": "unchecked-next-page"},
])
def test_registry_does_not_create_on_malformed_exact_search(model, bad_result):
    """불완전하거나 계약과 다른 정확한 이름 조회는 없는 모델로 보지 않는다."""
    class BrokenRegistrySearch(FakeClient):
        def get(self, endpoint, params):
            if endpoint == "registered-models/search":
                assert params["filter"] == f"name = '{registered_model_name(model)}'"
                return bad_result
            return super().get(endpoint, params)

    client = BrokenRegistrySearch()
    with pytest.raises(MLflowDeliveryError, match="RESPONSE-INVALID"):
        register_model_version(client, model, "run-1")
    assert client.models == []
    assert client.created == 0


@pytest.mark.parametrize("bad_result", [
    {"model_versions": ""},
    {"model_versions": {}},
    {"model_versions": [{"name": "wrong-model", "version": "1"}]},
    {"model_versions": ["not-a-model-version"]},
])
def test_registry_does_not_create_when_existing_versions_cannot_be_verified(model, bad_result):
    """기존 판본 전수 조회가 깨지면 재시도 중 중복 판본을 생성하지 않는다."""
    class BrokenVersionSearch(FakeClient):
        def get(self, endpoint, params):
            if endpoint == "model-versions/search":
                assert params["filter"] == f"name = '{registered_model_name(model)}'"
                return bad_result
            return super().get(endpoint, params)

    client = BrokenVersionSearch()
    with pytest.raises(MLflowDeliveryError, match="RESPONSE-INVALID"):
        register_model_version(client, model, "run-1")
    assert client.created == 0
    assert client.models == []
