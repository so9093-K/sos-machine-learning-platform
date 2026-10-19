"""실제 Model Revision·Scoring Result를 사용하는 비가법 점수 설명."""

from uuid import uuid4

from sqlalchemy import text

from sos_platform.worker import run_once


def _upload(client, name, content):
    result = client.post("/api/datasets/files", files={"file": (name, content, "text/csv")})
    assert result.status_code == 201, result.text
    return result.json()


def _submit(client, endpoint, data):
    return client.post(endpoint, json=data, headers={"Idempotency-Key": str(uuid4())})


def test_explanation_is_independent_and_matches_original_anomaly_score(workspace):
    client, store = workspace
    training = _upload(
        client,
        "explain-train.csv",
        b"temperature,pressure\n1.0,10.0\n2.0,11.0\n3.0,12.0\n4.0,13.0\n",
    )
    first = _submit(
        client,
        "/api/ml/train/jobs",
        {
            "dataset_id": training["id"],
            "features": ["temperature", "pressure"],
        },
    )
    assert first.status_code == 202
    assert run_once(store)
    finished = client.get(f"/api/ml/train/jobs/{first.json()['id']}").json()
    assert finished["status"] == "Succeeded", finished
    model_id = finished["result"]["model_revision_id"]
    runtime = client.get(f"/api/ml/models/{model_id}").json()["runtime"]
    assert len(runtime["feature_reference_medians"]) == 2
    input_data = _upload(
        client,
        "explain-scores.csv",
        b"temperature,pressure\n100.0,1000.0\n3.0,12.0\n",
    )
    score_job = _submit(
        client,
        "/api/ml/scoring/jobs",
        {
            "model_revision_id": model_id,
            "dataset_id": input_data["id"],
        },
    )
    assert score_job.status_code == 202
    assert run_once(store)
    completed = client.get(f"/api/ml/scoring/jobs/{score_job.json()['id']}").json()
    assert completed["status"] == "Succeeded", completed
    scoring_id = completed["result"]["scoring_result_id"]

    explain = _submit(
        client,
        "/api/ml/explanations/jobs",
        {
            "scoring_result_id": scoring_id,
            "row_index": 0,
        },
    )
    assert explain.status_code == 202, explain.text
    assert run_once(store)
    detailed = client.get(f"/api/ml/explanations/jobs/{explain.json()['id']}").json()
    assert detailed["status"] == "Succeeded", detailed
    revision_id = detailed["result"]["explanation_result_id"]
    found = client.get(f"/api/ml/explanations/{revision_id}")
    assert found.status_code == 200, found.text
    payload = found.json()["output"]
    assert payload["method"] == "median_reference_feature_replacement"
    assert payload["additive_attribution"] is False
    assert payload["row_index"] == 0
    assert [x["feature"] for x in payload["feature_sensitivities"]] == [
        "temperature",
        "pressure",
    ]
    assert all(
        isinstance(x["anomaly_score_delta"], float) for x in payload["feature_sensitivities"]
    )
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM explanation_results")).scalar_one() == 1


def test_explanation_rejects_nonexistent_row_without_job(workspace):
    client, store = workspace
    training = _upload(client, "no-row.csv", b"temperature\n1.0\n2.0\n3.0\n")
    job = _submit(
        client,
        "/api/ml/train/jobs",
        {
            "dataset_id": training["id"],
            "features": ["temperature"],
        },
    )
    assert run_once(store)
    training_id = client.get(f"/api/ml/train/jobs/{job.json()['id']}").json()["result"][
        "model_revision_id"
    ]
    scored = _submit(
        client,
        "/api/ml/scoring/jobs",
        {
            "dataset_id": training["id"],
            "model_revision_id": training_id,
        },
    )
    assert run_once(store)
    scoring_id = client.get(f"/api/ml/scoring/jobs/{scored.json()['id']}").json()["result"][
        "scoring_result_id"
    ]
    response = _submit(
        client,
        "/api/ml/explanations/jobs",
        {
            "scoring_result_id": scoring_id,
            "row_index": 9,
        },
    )
    assert response.status_code == 422, response.text
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM explanation_results")).scalar_one() == 0
