"""실제 PostgreSQL·S3·Worker 기반 ML 학습·모델 게시 계약."""

from uuid import uuid4

from sqlalchemy import text

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
