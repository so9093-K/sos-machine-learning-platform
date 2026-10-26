"""모델 그룹 분리·삭제 경계의 위험만 확인하는 가벼운 계약 테스트."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from sos_platform import model_deletion
from sos_platform.mlflow_errors import MLflowDeliveryError
from sos_platform.mlflow_registry import registered_model_name
from sos_platform.object_store import ObjectStore
from sos_platform.security import Actor


def _model(**patch):
    return {
        "owner_actor": "researcher-one",
        "algorithm": "isolation_forest",
        **patch,
    }


def test_model_groups_preserve_default_and_separate_named_models():
    default = registered_model_name(_model())
    assert default == registered_model_name(_model(registry_key="default"))
    other = registered_model_name(_model(registry_key="activity-risk"))
    assert other == default + "-activity-risk"
    assert registered_model_name(_model(owner_actor="other", registry_key="activity-risk")) != other
    with pytest.raises(MLflowDeliveryError):
        registered_model_name(_model(registry_key="../../untrusted"))


def test_s3_rejects_non_model_deletion():
    storage = object.__new__(ObjectStore)
    with pytest.raises(ValueError):
        storage.permanently_delete("datasets/user-parquet")
    with pytest.raises(ValueError):
        storage.permanently_delete("results/abc.scores.parquet")


def test_versioned_s3_delete_does_not_create_new_marker():
    storage = object.__new__(ObjectStore)
    storage.config = SimpleNamespace(s3_bucket="sos-test")
    state = [
        {"Key": "results/job/attempt.model.joblib", "VersionId": "one"},
        {"Key": "results/job/attempt.model.joblib", "VersionId": "two"},
    ]
    client = Mock()
    client.list_object_versions.side_effect = lambda **kwargs: {
        "Versions": list(state),
    }
    def delete_versions(**kwargs):
        refs = kwargs["Delete"]["Objects"]
        state[:] = [item for item in state if item not in refs]
        return {"Deleted": refs}
    client.delete_objects.side_effect = delete_versions
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.head_object.side_effect = RuntimeError("deleted")
    storage.client = client
    # S3 head_object must produce 404 to confirm absence.
    from botocore.exceptions import ClientError
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "HeadObject",
    )
    storage.permanently_delete("results/job/attempt.model.joblib")
    client.delete_object.assert_not_called()
    assert not state


def test_model_delete_does_not_mark_completed_when_s3_fails(monkeypatch):
    actor = Actor("researcher-one", "human", frozenset({"researcher"}))
    item = {
        "id": "model-id", "registry_model": "sos-if-test",
        "registry_version": "2", "location": "s3://bucket/results/job/attempt.model.joblib",
        "lifecycle_state": "Active",
    }
    calls = []
    monkeypatch.setattr(model_deletion, "begin_delete",
                        lambda store, model_id, user: (calls.append("begin") or item))
    monkeypatch.setattr(model_deletion, "finish_delete",
                        lambda store, model_id, user: calls.append("finish"))
    class Registry:
        def __init__(self, *args, **kwargs):
            pass
        def post(self, *args, **kwargs):
            calls.append("registry")
            return {}
    class Storage:
        def key(self, location):
            return "results/job/attempt.model.joblib"
        def permanently_delete(self, key):
            calls.append("s3")
            raise OSError("offline")
    monkeypatch.setattr(model_deletion, "MLflowClient", Registry)
    monkeypatch.setattr(model_deletion, "ObjectStore", Storage)
    monkeypatch.setattr(model_deletion, "MLflowSettings",
                        SimpleNamespace(from_env=lambda: object()))
    router = model_deletion.model_deletion_router(object())
    endpoint = next(route.endpoint for route in router.routes
                    if getattr(route, "path", "") == "/api/ml/models/{revision_id}")
    with pytest.raises(HTTPException) as error:
        endpoint("model-id", confirm=True, actor=actor)
    assert error.value.status_code == 503
    assert calls == ["begin", "registry", "s3"]
