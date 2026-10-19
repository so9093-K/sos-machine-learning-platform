"""저장된 Isolation Forest를 실제 S3에서 복원해 다른 Dataset에 적용한다."""

from uuid import uuid4

import polars as pl
from sqlalchemy import text

from sos_platform.object_store import ObjectStore
from sos_platform.worker import run_once


def upload_sensor_dataset(client, values: bytes) -> dict:
    response = client.post(
        "/api/datasets/files",
        files={"file": ("sensor-score.csv", values, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_scoring_result_is_independent_from_training_and_source_dataset(workspace, tmp_path):
    client, store = workspace
    training = upload_sensor_dataset(
        client,
        b"temperature\n1.0\n2.0\n3.0\n4.0\n5.0\n",
    )
    trained = client.post(
        "/api/ml/train/jobs",
        json={
            "dataset_id": training["id"],
            "features": ["temperature"],
            "params": {"n_estimators": 20},
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert trained.status_code == 202, trained.text
    assert run_once(store)
    train_job = client.get(f"/api/ml/train/jobs/{trained.json()['id']}").json()
    assert train_job["status"] == "Succeeded", train_job
    model_id = train_job["result"]["model_revision_id"]
    model = client.get(f"/api/ml/models/{model_id}").json()
    assert model["runtime"]["feature_dtypes"] == ["Float64"]

    scoring = upload_sensor_dataset(
        client,
        b"temperature,is_anomaly\n1.0,0\n2.0,0\n3.0,0\n100.0,1\n",
    )
    key = str(uuid4())
    request = {
        "model_revision_id": model_id,
        "dataset_id": scoring["id"],
        "label_column": "is_anomaly",
    }
    first = client.post("/api/ml/scoring/jobs", json=request, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    again = client.post("/api/ml/scoring/jobs", json=request, headers={"Idempotency-Key": key})
    assert again.json()["id"] == first.json()["id"]
    job_id = first.json()["id"]
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404
    assert run_once(store)
    job = client.get(f"/api/ml/scoring/jobs/{job_id}").json()
    assert job["status"] == "Succeeded", job
    result_id = job["result"]["scoring_result_id"]
    scored = client.get(f"/api/ml/scoring/results/{result_id}")
    assert scored.status_code == 200, scored.text
    result = scored.json()
    assert result["model_revision_id"] == model_id
    assert result["dataset_id"] == scoring["id"]
    assert result["dataset_version"] == scoring["version"]
    assert result["score_summary"]["scope"] == "scoring_dataset"
    assert result["score_summary"]["training_data_reused"] is False
    assert result["ranking_metrics"]["positive_count"] == 1
    assert result["ranking_metrics"]["average_precision"] is not None
    storage = ObjectStore()
    local = tmp_path / "scored.parquet"
    storage.get(storage.key(result["location"]), local, expected_sha256=result["sha256"])
    frame = pl.read_parquet(local)
    assert frame.height == 4
    assert frame["label"].to_list() == [0, 0, 0, 1]
    assert frame["row_index"].to_list() == [0, 1, 2, 3]
    assert frame["features"].struct.field("temperature").to_list() == [1, 2, 3, 100]
    observed = store.observe_objects(
        [{"location": result["location"], "fingerprint": "scoring-generation"}], 24
    )
    assert observed[result["location"]]["referenced"] is True
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM scoring_results")).scalar_one() == 1
        assert (
            db.execute(
                text("SELECT COUNT(*) FROM datasets WHERE id=:id"),
                {"id": scoring["id"]},
            ).scalar_one()
            == 1
        )


def test_scoring_invalid_feature_schema_does_not_publish(workspace):
    client, store = workspace
    training = upload_sensor_dataset(client, b"temperature\n1.0\n2.0\n3.0\n")
    submitted = client.post(
        "/api/ml/train/jobs",
        json={"dataset_id": training["id"], "features": ["temperature"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert run_once(store)
    job = client.get(f"/api/ml/train/jobs/{submitted.json()['id']}").json()
    assert job["status"] == "Succeeded"
    model_id = job["result"]["model_revision_id"]
    # 등록된 모델의 Float64 Feature를 정수 Dataset에 적용할 때 침묵하는 dtype 전환을 막는다.
    scoring = upload_sensor_dataset(client, b"temperature\n1\n2\n3\n")
    queued = client.post(
        "/api/ml/scoring/jobs",
        json={"model_revision_id": model_id, "dataset_id": scoring["id"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert queued.status_code == 202
    assert run_once(store)
    finished = client.get(f"/api/ml/scoring/jobs/{queued.json()['id']}").json()
    assert finished["status"] == "Failed"
    assert finished["error_code"] == "SOS-ML-INPUT-INVALID"
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM scoring_results")).scalar_one() == 0
