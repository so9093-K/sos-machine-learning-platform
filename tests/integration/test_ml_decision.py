"""실제 S3 Scoring Result에 대한 독립 정책·평가 Job 및 판본."""

from uuid import uuid4

from sqlalchemy import text

from sos_platform.worker import run_once


def _upload(client, name, content):
    res = client.post(
        "/api/datasets/files",
        files={"file": (name, content, "text/csv")},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _submit(client, endpoint, body):
    return client.post(endpoint, json=body, headers={"Idempotency-Key": str(uuid4())})


def test_decision_policy_revisions_and_evaluation_reuse_same_scoring_artifact(workspace):
    client, store = workspace
    data = _upload(client, "ml_train.csv", b"temperature\n1.0\n2.0\n3.0\n4.0\n")
    trained = _submit(
        client,
        "/api/ml/train/jobs",
        {
            "dataset_id": data["id"],
            "features": ["temperature"],
        },
    )
    assert trained.status_code == 202
    assert run_once(store)
    model_id = client.get(f"/api/ml/train/jobs/{trained.json()['id']}").json()["result"][
        "model_revision_id"
    ]
    val = _upload(
        client,
        "ml_eval.csv",
        b"temperature,abnormal\n1.0,0\n2.0,0\n3.0,1\n200.0,1\n",
    )
    score_job = _submit(
        client,
        "/api/ml/scoring/jobs",
        {
            "dataset_id": val["id"],
            "model_revision_id": model_id,
            "label_column": "abnormal",
        },
    )
    assert score_job.status_code == 202, score_job.text
    assert run_once(store)
    result_id = client.get(f"/api/ml/scoring/jobs/{score_job.json()['id']}").json()["result"][
        "scoring_result_id"
    ]
    before_training = client.get(f"/api/ml/models/{model_id}").json()
    thresholds = []
    policy_ids = []
    for body in [
        {"mode": "fixed", "threshold": 0.5},
        {"mode": "quantile", "alert_fraction": 0.25},
    ]:
        sent = _submit(
            client,
            "/api/ml/policies/jobs",
            {
                "scoring_result_id": result_id,
                **body,
            },
        )
        assert sent.status_code == 202, sent.text
        assert run_once(store)
        job = client.get(f"/api/ml/policies/jobs/{sent.json()['id']}").json()
        assert job["status"] == "Succeeded", job
        item = client.get(f"/api/ml/policies/{job['result']['policy_revision_id']}")
        assert item.status_code == 200, item.text
        value = item.json()
        thresholds.append(value["threshold"])
        policy_ids.append(value["id"])
    assert thresholds[0] != thresholds[1]
    assert before_training == client.get(f"/api/ml/models/{model_id}").json()

    result = _submit(
        client,
        "/api/ml/evaluations/jobs",
        {
            "scoring_result_id": result_id,
            "policy_revision_id": policy_ids[0],
        },
    )
    assert result.status_code == 202, result.text
    assert run_once(store)
    job = client.get(f"/api/ml/evaluations/jobs/{result.json()['id']}").json()
    assert job["status"] == "Succeeded", job
    ev = client.get(f"/api/ml/evaluations/{job['result']['evaluation_revision_id']}")
    assert ev.status_code == 200
    item = ev.json()
    assert item["label_coverage"] == "full"
    assert item["decision_metrics"]["precision"] is not None
    assert item["ranking_metrics"]["average_precision"] is not None
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM model_revisions")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM scoring_results")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM decision_policy_revisions")).scalar_one() == 2
        assert db.execute(text("SELECT COUNT(*) FROM evaluation_revisions")).scalar_one() == 1


def test_validation_rejects_wrong_policy_combination(workspace):
    client, _ = workspace
    for spec in [
        {"mode": "fixed"},
        {"mode": "fixed", "threshold": "NaN"},
        {"mode": "quantile", "alert_fraction": 1},
        {"mode": "quantile", "threshold": 0.4, "alert_fraction": 0.1},
    ]:
        response = client.post(
            "/api/ml/policies/jobs",
            json={"scoring_result_id": str(uuid4()), **spec},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 422, (spec, response.text)
