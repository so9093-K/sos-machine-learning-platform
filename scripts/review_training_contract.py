"""임시 검토: 동일 HTTP 검증을 main과 PR 코드에서 직접 실행한다."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sos_platform import api
from sos_platform.security import Actor, IdentitySettings

params = {"n_estimators": 100, "max_samples": "auto", "max_features": 1.0,
          "bootstrap": False, "random_state": 42}
dataset = {"id": "d1", "version": "v1", "owner_actor": "alice"}
job = {
    "id": "j1", "kind": "ml_train", "dataset_id": "d1",
    "spec": {"algorithm": "isolation_forest", "dataset_version": "v1",
             "features": ["x"], "params": params},
    "status": "Succeeded", "cancel_requested": False, "result": None,
    "error": None, "created_at": "2026-10-20T00:00:00Z",
    "updated_at": "2026-10-20T00:00:00Z", "attempts": [],
    "owner_actor": "alice",
}
users = {
    "alice": Actor("alice", "human", frozenset({"researcher"})),
    "bob": Actor("bob", "human", frozenset({"researcher"})),
    "viewer": Actor("viewer", "human", frozenset({"viewer"})),
    "admin": Actor("admin", "human", frozenset({"administrator"})),
}
tokens = {name: (name * 40)[:48] for name in users}
identity = IdentitySettings("token", {
    hashlib.sha256(value.encode()).hexdigest(): users[name]
    for name, value in tokens.items()
})
store = Mock()
store.get_dataset.side_effect = lambda _: dataset
store.get_job.side_effect = lambda _: job
store.create_job.side_effect = lambda **_: dict(job, status="Queued")
store.request_cancel.side_effect = lambda *_, **__: dict(job, cancel_requested=True)
store.jobs.return_value = []

def auth(name):
    return {"Authorization": "Bearer " + tokens[name]}

def key(name):
    return {**auth(name), "Idempotency-Key": "review-training-key-123"}

results = {}
def probe(client, title, method, path, expected=None, **kwargs):
    response = client.request(method, path, **kwargs)
    results[title] = response.status_code
    if expected is not None:
        assert response.status_code == expected, (title, response.status_code, response.text[:500])
    return response

with TemporaryDirectory() as home:
    with patch.object(api, "Store", return_value=store):
        app = api.create_app(Path(home), identity=identity)
    with TestClient(app) as client:
        route_paths = sorted(
            ("/" + ",".join(sorted(r.methods or [])) + ":" + r.path)
            for r in app.routes if r.path.startswith("/api/ml/train/")
        )
        assert len(route_paths) == 4, route_paths  # 5 operations; GET/POST share jobs path
        results["routes"] = route_paths

        accepted = probe(client, "train_valid", "POST", "/api/ml/train/jobs",
                         202, headers=key("alice"), json={"dataset_id":"d1","features":["x"]})
        assert accepted.json()["kind"] == "ml_train"
        train_args = store.create_job.call_args.kwargs
        assert train_args["owner_actor"] == "alice"
        assert train_args["idempotency_key"] == "review-training-key-123"
        assert train_args["spec"]["dataset_version"] == "v1"
        store.create_job.reset_mock()

        missing = probe(client, "train_no_key", "POST", "/api/ml/train/jobs",
                        headers=auth("alice"), json={"dataset_id":"d1","features":["x"]})
        assert 400 <= missing.status_code < 500
        assert not store.create_job.called
        probe(client, "train_foreign", "POST", "/api/ml/train/jobs",
              404, headers=key("bob"), json={"dataset_id":"d1","features":["x"]})
        assert not store.create_job.called
        probe(client, "train_read_only", "POST", "/api/ml/train/jobs",
              403, headers=key("viewer"), json={"dataset_id":"d1","features":["x"]})
        probe(client, "train_bad_key", "POST", "/api/ml/train/jobs",
              422, headers={**auth("alice"),"Idempotency-Key":"no"},
              json={"dataset_id":"d1","features":["x"]})
        probe(client, "train_bad_features", "POST", "/api/ml/train/jobs",
              422, headers=key("alice"), json={"dataset_id":"d1","features":["x","x"]})

        lst = probe(client, "list_owner", "GET", "/api/ml/train/jobs", 200, headers=auth("alice"))
        assert lst.json()["items"] == []
        assert store.jobs.call_args.kwargs["actor_id"] == "alice"
        probe(client, "list_admin", "GET", "/api/ml/train/jobs", 200, headers=auth("admin"))
        assert store.jobs.call_args.kwargs["actor_id"] is None
        probe(client, "list_anonymous", "GET", "/api/ml/train/jobs", 401)
        probe(client, "list_bad_limit", "GET", "/api/ml/train/jobs?limit=0", 422, headers=auth("alice"))

        probe(client, "detail_owner", "GET", "/api/ml/train/jobs/j1", 200, headers=auth("alice"))
        probe(client, "detail_foreign", "GET", "/api/ml/train/jobs/j1", 404, headers=auth("bob"))
        store.get_job.side_effect = lambda _: dict(job, kind="ml_score")
        probe(client, "detail_wrong_kind", "GET", "/api/ml/train/jobs/j1", 404, headers=auth("alice"))
        store.get_job.side_effect = lambda _: job

        probe(client, "fork_valid", "POST", "/api/ml/train/jobs/j1/fork",
              202, headers=key("alice"), json={})
        fork_args = store.create_job.call_args.kwargs
        assert fork_args["spec"]["forked_from_job_id"] == "j1"
        assert fork_args["spec"]["features"] == ["x"]
        store.create_job.reset_mock()
        probe(client, "fork_foreign", "POST", "/api/ml/train/jobs/j1/fork",
              404, headers=key("bob"), json={})
        assert not store.create_job.called
        store.get_job.side_effect = lambda _: dict(job, status="Running")
        probe(client, "fork_not_complete", "POST", "/api/ml/train/jobs/j1/fork",
              409, headers=key("alice"), json={})
        store.get_job.side_effect = lambda _: job
        store.get_dataset.side_effect = lambda _: dict(dataset, version="changed")
        probe(client, "fork_changed_dataset", "POST", "/api/ml/train/jobs/j1/fork",
              409, headers=key("alice"), json={})
        store.get_dataset.side_effect = lambda _: dataset

        probe(client, "cancel_valid", "POST", "/api/ml/train/jobs/j1/cancel",
              200, headers=auth("alice"))
        store.request_cancel.assert_called()
        store.request_cancel.reset_mock()
        probe(client, "cancel_foreign", "POST", "/api/ml/train/jobs/j1/cancel",
              404, headers=auth("bob"))
        assert not store.request_cancel.called
        probe(client, "cancel_viewer", "POST", "/api/ml/train/jobs/j1/cancel",
              403, headers=auth("viewer"))
        assert not store.request_cancel.called

print(json.dumps(results, sort_keys=True))
