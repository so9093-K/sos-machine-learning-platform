"""모델 그룹 분리·삭제 경계의 위험만 확인하는 가벼운 계약 테스트."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from sos_platform import model_deletion
from sos_platform.mlflow_errors import MLflowDeliveryError
from sos_platform.mlflow_registry import registered_model_name
from sos_platform.mlflow_tracking import MLflowClient
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
        "id": "model-id", "job_id": "job", "attempt_id": "attempt",
        "registry_model": "sos-if-test", "registry_version": "2",
        "location": "s3://bucket/results/job/attempt.model.joblib",
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
        def delete(self, *args, **kwargs):
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


def test_mlflow_management_uses_patch_and_delete_http_methods():
    class Response:
        def __init__(self, content):
            self.content = content
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            return self.content

    class Opener:
        def __init__(self):
            self.methods = []
        def open(self, request, timeout):
            self.methods.append(request.get_method())
            return Response(b"{}" if request.get_method() == "PATCH" else b"")

    client = MLflowClient(SimpleNamespace(
        endpoint="http://127.0.0.1:5000", token=None, timeout_seconds=1,
    ))
    opener = Opener()
    client._opener = opener
    assert client.patch("registered-models/update", {"name":"sos-if-test"}) == {}
    assert client.delete("model-versions/delete", {"name":"sos-if-test", "version":"1"}) == {}
    assert opener.methods == ["PATCH", "DELETE"]


def test_deleted_model_cannot_be_reloaded_into_runtime(tmp_path):
    from sos_platform.ml_runtime import load_trusted_isolation_forest

    with pytest.raises(ValueError, match="Deleted or deleting"):
        load_trusted_isolation_forest({"lifecycle_state": "Deleted"}, tmp_path)
    with pytest.raises(ValueError, match="Deleted or deleting"):
        load_trusted_isolation_forest({"lifecycle_state": "Deleting"}, tmp_path)


def test_unconfigured_mlflow_does_not_begin_model_deletion(monkeypatch):
    from sos_platform import model_deletion
    from sos_platform.model_deletion import model_deletion_router

    actor = Actor("researcher-one", "human", frozenset({"researcher"}))
    monkeypatch.setattr(model_deletion.MLflowSettings, "from_env", lambda: None)
    monkeypatch.setattr(model_deletion, "begin_delete",
                        lambda *args: pytest.fail("Unconfigured MLflow must not mark Deleting"))
    router = model_deletion_router(object())
    endpoint = next(route.endpoint for route in router.routes
                    if getattr(route, "path", "") == "/api/ml/models/{revision_id}")
    with pytest.raises(HTTPException) as error:
        endpoint("model-id", confirm=True, actor=actor)
    assert error.value.status_code == 503


def test_invalid_model_object_key_never_deletes_registry_version(monkeypatch):
    actor = Actor("researcher-one", "human", frozenset({"researcher"}))
    model = {
        "id": "model-id", "job_id": "job", "attempt_id": "attempt",
        "registry_model": "sos-if-test", "registry_version": "2",
        "location": "s3://bucket/results/unrelated.model.joblib",
        "lifecycle_state": "Active",
    }
    monkeypatch.setattr(model_deletion, "begin_delete", lambda *_: model)
    monkeypatch.setattr(model_deletion, "finish_delete", lambda *_: pytest.fail(
        "Mismatched artifact must not be marked deleted"))
    monkeypatch.setattr(model_deletion.MLflowSettings, "from_env", lambda: object())
    class Storage:
        def key(self, location):
            return location.split("s3://bucket/", 1)[1]
        def permanently_delete(self, key):
            pytest.fail("Wrong artifact must never be deleted")
    class Registry:
        def __init__(self, *args, **kwargs):
            pytest.fail("Registry must not be changed before artifact validation")
    monkeypatch.setattr(model_deletion, "ObjectStore", Storage)
    monkeypatch.setattr(model_deletion, "MLflowClient", Registry)
    router = model_deletion.model_deletion_router(object())
    endpoint = next(route.endpoint for route in router.routes
                    if getattr(route, "path", "") == "/api/ml/models/{revision_id}")
    with pytest.raises(HTTPException) as err:
        endpoint("model-id", confirm=True, actor=actor)
    assert err.value.status_code == 503


@pytest.mark.parametrize("state", ["Active", "Deleting"])
def test_offered_model_requires_withdrawal_before_deletion(state):
    from contextlib import nullcontext

    actor = Actor("supplier", "service", frozenset({"automation"}))
    db = Mock()
    row = {"id": "model", "owner_actor": actor.id, "lifecycle_state": state}
    db.execute.side_effect = [
        Mock(**{"mappings.return_value.one_or_none.return_value": row}),
        Mock(**{"scalar_one_or_none.return_value": 1}),
    ]
    store = SimpleNamespace(connection=lambda: nullcontext(db))
    with pytest.raises(HTTPException) as error:
        model_deletion.begin_delete(store, "model", actor)
    assert error.value.status_code == 409
    assert "먼저 모델 제공을 중단" in error.value.detail
    assert db.execute.call_count == 2  # 외부 삭제나 상태 전환 전에 거부한다.


@pytest.mark.parametrize("state", ["Deleting", "Deleted"])
def test_deleting_or_deleted_model_cannot_be_offered(state):
    from contextlib import nullcontext
    from sos_platform.store import Store
    from sos_platform.store_models import ModelOfferingUnavailable

    db = Mock()
    db.execute.return_value.mappings.return_value.first.return_value = {
        "owner_actor": "supplier", "lifecycle_state": state, "registry_version": "1",
    }
    store = object.__new__(Store)
    store.connection = lambda: nullcontext(db)
    with pytest.raises(ModelOfferingUnavailable):
        store.offer_model("model", description="설명", actor_id="supplier",
                          actor_type="service", owner_actor="supplier")
    assert db.execute.call_count == 1


@pytest.mark.parametrize("kind", ["ml_score", "ml_playground"])
def test_withdrawal_is_rechecked_when_the_job_is_committed(kind):
    from contextlib import nullcontext
    from sos_platform.errors import PlatformError
    from sos_platform.store import Store

    db = Mock()
    db.execute.side_effect = [
        Mock(**{"scalar_one_or_none.return_value": "Active"}),
        Mock(**{"scalar_one_or_none.return_value": None}),
    ]
    store = object.__new__(Store)
    store.connection = lambda: nullcontext(db)
    with pytest.raises(PlatformError) as error:
        store.create_job(kind=kind, dataset_id="user-data",
            spec={"model_revision_id": "model"}, owner_actor="consumer",
            require_model_offering=True)
    assert error.value.code == "SOS-API-CONFLICT"
    assert db.execute.call_count == 2
