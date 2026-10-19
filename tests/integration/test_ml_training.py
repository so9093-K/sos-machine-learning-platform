"""실제 PostgreSQL·S3·Worker 기반 ML 학습·모델 게시 계약."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from sos_platform.api import create_app
from sos_platform.object_store import ObjectStore
from sos_platform.worker import run_once

from .support import upload


def _submit(client, dataset: dict, *, key: str | None = None):
    return client.post(
        "/api/ml/train/jobs",
        json={
            "dataset_id": dataset["id"],
            "features": ["temperature"],
            "params": {"n_estimators": 10, "random_state": 17},
        },
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_model_revision_publication_with_job_and_object_intent(workspace):
    client, store = workspace
    dataset = upload(client)
    key = str(uuid4())
    queued = _submit(client, dataset, key=key)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["id"]
    repeated = _submit(client, dataset, key=key)
    assert repeated.status_code == 202
    assert repeated.json()["id"] == job_id
    assert client.get(f"/api/ml/train/jobs/{job_id}").json()["status"] == "Queued"
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert job_id not in [i["id"] for i in client.get("/api/jobs").json()["items"]]

    assert run_once(store) is True
    finished = client.get(f"/api/ml/train/jobs/{job_id}").json()
    assert finished["status"] == "Succeeded", finished
    assert finished["attempts"][0]["status"] == "Succeeded"
    revision_id = finished["result"]["model_revision_id"]
    response = client.get(f"/api/ml/models/{revision_id}")
    assert response.status_code == 200, response.text
    model = response.json()
    assert model["dataset_id"] == dataset["id"]
    assert model["dataset_version"] == dataset["version"]
    assert model["features"] == ["temperature"]
    assert model["training_score_summary"]["scope"] == "training_data_only"
    assert model["training_score_summary"]["score_count"] == 3
    assert model["runtime"]["python"]
    assert model["runtime"]["scikit_learn"]
    assert model["runtime"]["numpy"]
    assert "precision" not in model["training_score_summary"]
    # Object Health가 불변 모델을 미참조 또는 정리 후보로 오판하지 않는다.
    metadata = store.observe_objects(
        [{"location": model["location"], "fingerprint": "model-object-generation"}], 24
    )[model["location"]]
    assert metadata["referenced"] is True
    from sos_platform.config import StoragePolicy
    from sos_platform.reconciler import classify_object

    status, reason = classify_object(
        {"modified_at": model["created_at"], "fingerprint": "model-object-generation"},
        metadata,
        StoragePolicy(),
    )
    assert status == "protected"
    assert "Model Revision" in reason
    # 게시 의도와 Model Revision 및 Job 성공은 같은 정본에 있어야 한다.
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 1
        assert (
            db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE action='model.publish' AND resource_id=:revision"
                ),
                {"revision": revision_id},
            ).scalar_one()
            == 1
        )
        assert (
            db.execute(
                text("SELECT COUNT(*) FROM object_writes WHERE state='Published'")
            ).scalar_one()
            == 2
        )  # 원본 파일과 학습 모델
    location = model["location"]
    s3 = ObjectStore()
    key_name = s3.key(location)
    assert s3.client.head_object(Bucket=s3.config.s3_bucket, Key=key_name)["ContentLength"] > 0


def test_training_cancel_before_claim_and_invalid_feature(workspace):
    client, store = workspace
    dataset = upload(client)
    bad = client.post(
        "/api/ml/train/jobs",
        json={
            "dataset_id": dataset["id"],
            "features": ["missing"],
        },
    )
    assert bad.status_code == 202
    assert run_once(store) is True
    job = client.get(f"/api/ml/train/jobs/{bad.json()['id']}").json()
    assert job["status"] == "Failed"
    assert job["error_code"] == "SOS-ML-INPUT-INVALID"
    assert job["result"] is None
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 0
    queued = _submit(client, dataset)
    assert queued.status_code == 202
    cancelled = client.post(f"/api/ml/train/jobs/{queued.json()['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "Cancelled"


def test_model_publish_failure_does_not_commit_revision(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    response = _submit(client, dataset)
    assert response.status_code == 202

    original_put = ObjectStore.put

    def fail_model(self, key, source):
        if key.endswith(".model.joblib"):
            raise OSError("simulated write failure")
        return original_put(self, key, source)

    monkeypatch.setattr(ObjectStore, "put", fail_model)
    assert run_once(store)
    result = client.get(f"/api/ml/train/jobs/{response.json()['id']}").json()
    assert result["status"] == "Failed"
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 0
        assert (
            db.execute(text("SELECT COUNT(*) FROM object_writes WHERE state='Failed'")).scalar_one()
            == 1
        )


def test_training_parameters_reject_implicit_types_before_enqueuing(workspace):
    client, store = workspace
    dataset = upload(client)
    for params in (
        {"max_samples": True},
        {"max_samples": "0.5"},
        {"max_features": False},
        {"max_features": "1"},
        {"bootstrap": "false"},
        {"n_estimators": True},
        {"max_samples": -1},
    ):
        result = client.post(
            "/api/ml/train/jobs",
            json={"dataset_id": dataset["id"], "features": ["temperature"], "params": params},
        )
        assert result.status_code == 422, (params, result.text)
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == 0


def test_ml_model_revision_token_ownership_and_readonly_role(workspace, monkeypatch):
    """모델 ID 추측·이력 조회·학습 제출에 기존 RBAC와 소유권을 적용한다."""
    _, store = workspace
    users = {
        "alice": {"actor_id": "alice", "kind": "human", "roles": ["researcher"]},
        "bob": {"actor_id": "bob", "kind": "human", "roles": ["viewer"]},
        "ops": {"actor_id": "ops", "kind": "human", "roles": ["operator"]},
    }
    tokens = {name: "sos-ml-" + name + "-" * 45 for name in users}
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps({hashlib.sha256(tokens[k].encode()).hexdigest(): v for k, v in users.items()}),
    )
    with TestClient(create_app(store.home)) as secure:

        def auth(name):
            return {"Authorization": "Bearer " + tokens[name]}

        uploaded = secure.post(
            "/api/datasets/files",
            files={"file": ("ml.csv", b"temperature\n1\n2\n3\n4\n", "text/csv")},
            headers=auth("alice"),
        )
        assert uploaded.status_code == 201, uploaded.text
        body = {"dataset_id": uploaded.json()["id"], "features": ["temperature"]}
        assert (
            secure.post("/api/ml/train/jobs", json=body, headers=auth("alice")).status_code == 428
        )
        assert (
            secure.post(
                "/api/ml/train/jobs",
                json=body,
                headers={**auth("bob"), "Idempotency-Key": "viewer-training-01"},
            ).status_code
            == 403
        )
        submitted = secure.post(
            "/api/ml/train/jobs",
            json=body,
            headers={**auth("alice"), "Idempotency-Key": "alice-training-01"},
        )
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["id"]
        assert secure.get("/api/ml/train/jobs", headers=auth("bob")).json()["items"] == []
        assert secure.get(f"/api/ml/train/jobs/{job_id}", headers=auth("bob")).status_code == 404
        assert (
            secure.post(f"/api/ml/train/jobs/{job_id}/cancel", headers=auth("bob")).status_code
            == 403
        )
        assert secure.get(f"/api/ml/train/jobs/{job_id}", headers=auth("ops")).status_code == 403
        assert run_once(store) is True
        completed = secure.get(f"/api/ml/train/jobs/{job_id}", headers=auth("alice")).json()
        assert completed["status"] == "Succeeded"
        revision_id = completed["result"]["model_revision_id"]
        assert secure.get(f"/api/ml/models/{revision_id}", headers=auth("alice")).status_code == 200
        assert secure.get(f"/api/ml/models/{revision_id}", headers=auth("bob")).status_code == 404
        assert secure.get(f"/api/ml/models/{revision_id}", headers=auth("ops")).status_code == 403


def test_training_cancellation_after_s3_write_does_not_publish_model(workspace, monkeypatch):
    """S3 바이트 업로드와 완료 트랜잭션 사이 취소가 들어오면 판본을 확정하지 않는다."""
    client, store = workspace
    dataset = upload(client)
    queued = _submit(client, dataset)
    assert queued.status_code == 202
    uploaded = Event()
    release = Event()
    original_put = ObjectStore.put

    def pause_after_model_upload(self, key, source):
        version = original_put(self, key, source)
        if key.endswith(".model.joblib"):
            uploaded.set()
            assert release.wait(timeout=30)
        return version

    monkeypatch.setattr(ObjectStore, "put", pause_after_model_upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        finished = pool.submit(run_once, store)
        try:
            assert uploaded.wait(timeout=30), "Model was not uploaded in time"
            requested = client.post(f"/api/ml/train/jobs/{queued.json()['id']}/cancel")
            assert requested.status_code == 200, requested.text
            assert requested.json()["cancel_requested"] is True
        finally:
            release.set()
        assert finished.result(timeout=30) is True
    final = client.get(f"/api/ml/train/jobs/{queued.json()['id']}").json()
    assert final["status"] == "Cancelled"
    assert final["result"] is None
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 0
        assert (
            db.execute(text("SELECT COUNT(*) FROM object_writes WHERE state='Failed'")).scalar_one()
            == 1
        )


def test_training_model_db_failure_keeps_unpublished_object(workspace, monkeypatch):
    """모델 바이트가 존재해도 DB 완료가 실패하면 Model Revision은 보이지 않는다."""
    client, store = workspace
    dataset = upload(client)
    queued = _submit(client, dataset)
    assert queued.status_code == 202
    finish = store.finish

    def reject_model_completion(job_id, status, **kwargs):
        if status == "Succeeded" and kwargs.get("model_publish"):
            raise RuntimeError("simulated model transaction failure")
        return finish(job_id, status, **kwargs)

    monkeypatch.setattr(store, "finish", reject_model_completion)
    assert run_once(store)
    job = store.get_job(queued.json()["id"])
    assert job["status"] == "Failed"
    assert job["result"] is None
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 0
    location = ObjectStore().location(
        f"results/{job['id']}/{job['attempts'][-1]['id']}.model.joblib"
    )
    assert store.object_writes(location)[0]["state"] == "Failed"
    storage = ObjectStore()
    assert (
        storage.client.head_object(Bucket=storage.config.s3_bucket, Key=storage.key(location))[
            "ContentLength"
        ]
        > 0
    )


def test_training_fork_preserves_input_revision_and_provenance(workspace):
    client, store = workspace
    dataset = upload(client)
    first = _submit(client, dataset)
    assert first.status_code == 202
    blocked = client.post(
        f"/api/ml/train/jobs/{first.json()['id']}/fork",
        json={},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert blocked.status_code == 409
    assert run_once(store)
    original = client.get(f"/api/ml/train/jobs/{first.json()['id']}").json()
    assert original["status"] == "Succeeded"

    fork = client.post(
        f"/api/ml/train/jobs/{original['id']}/fork",
        json={"params": {"n_estimators": 25, "random_state": 17}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert fork.status_code == 202, fork.text
    copied = fork.json()
    assert copied["id"] != original["id"]
    assert copied["spec"]["forked_from_job_id"] == original["id"]
    assert copied["spec"]["features"] == original["spec"]["features"]
    assert copied["spec"]["dataset_version"] == original["spec"]["dataset_version"]
    assert copied["spec"]["params"]["n_estimators"] == 25
    assert run_once(store)
    finished = client.get(f"/api/ml/train/jobs/{copied['id']}").json()
    assert finished["status"] == "Succeeded", finished
    assert finished["result"]["model_revision_id"] != original["result"]["model_revision_id"]
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 2
