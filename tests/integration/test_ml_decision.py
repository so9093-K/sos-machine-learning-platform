"""실제 S3 Scoring Result에 대한 독립 정책·평가 Job 및 판본."""

import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from sos_platform.api import create_app
from sos_platform.security import Actor, IdentitySettings
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
        assert client.post(f"/api/jobs/{sent.json()['id']}/cancel").status_code == 404
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
    assert client.post(f"/api/jobs/{result.json()['id']}/cancel").status_code == 404
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


def test_ml_result_job_cancellation_keeps_results_unpublished(workspace):
    """정책·평가·설명 작업별로 일반 Job 경계를 유지하며 취소 상태를 확정한다."""
    client, store = workspace
    data = _upload(client, "cancel-ml.csv", b"temperature\n1.0\n2.0\n3.0\n4.0\n")
    trained = _submit(
        client,
        "/api/ml/train/jobs",
        {"dataset_id": data["id"], "features": ["temperature"]},
    )
    assert trained.status_code == 202
    assert run_once(store)
    model_id = client.get(f"/api/ml/train/jobs/{trained.json()['id']}").json()["result"][
        "model_revision_id"
    ]
    scored = _submit(
        client,
        "/api/ml/scoring/jobs",
        {"dataset_id": data["id"], "model_revision_id": model_id},
    )
    assert scored.status_code == 202
    assert run_once(store)
    scoring_id = client.get(f"/api/ml/scoring/jobs/{scored.json()['id']}").json()["result"][
        "scoring_result_id"
    ]
    cases = [
        ("policies", {"scoring_result_id": scoring_id, "mode": "fixed", "threshold": 0.5}),
        ("evaluations", {"scoring_result_id": scoring_id}),
        ("explanations", {"scoring_result_id": scoring_id, "row_index": 0}),
    ]
    for kind, body in cases:
        queued = _submit(client, f"/api/ml/{kind}/jobs", body)
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["id"]
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404
        other = next(name for name, _ in cases if name != kind)
        assert client.post(f"/api/ml/{other}/jobs/{job_id}/cancel").status_code == 404
        canceled = client.post(f"/api/ml/{kind}/jobs/{job_id}/cancel")
        assert canceled.status_code == 200, canceled.text
        assert canceled.json()["status"] == "Cancelled"
        assert canceled.json()["result"] is None
        assert client.get(f"/api/ml/{kind}/jobs/{job_id}").json()["status"] == "Cancelled"
        assert client.post(f"/api/ml/{kind}/jobs/{job_id}/cancel").status_code == 200
    assert run_once(store) is False
    with store.connection() as db:
        for table in ("decision_policy_revisions", "evaluation_revisions", "explanation_results"):
            assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0


def test_ml_result_cancel_checks_actor_permission_and_ownership(workspace):
    """소유자가 아닌 연구자에게는 작업 존재를 숨기고 Viewer는 기능 자체를 거부한다."""
    client, store = workspace
    dataset = _upload(client, "owner.csv", b"temperature\n1.0\n2.0\n")
    queued = store.create_job(
        kind="ml_policy",
        dataset_id=dataset["id"],
        spec={"scoring_result_id": "not-executed", "mode": "fixed", "threshold": 0.5},
        owner_actor="alice",
        actor_type="human",
    )
    tokens = {
        name: ("ml-result-auth-" + name).ljust(45, "-") for name in ("alice", "bob", "viewer")
    }
    actors = {
        "alice": Actor("alice", "human", frozenset({"researcher"})),
        "bob": Actor("bob", "human", frozenset({"researcher"})),
        "viewer": Actor("viewer", "human", frozenset({"viewer"})),
    }
    subjects = {
        hashlib.sha256(value.encode()).hexdigest(): actors[name] for name, value in tokens.items()
    }
    with TestClient(create_app(store.home, identity=IdentitySettings("token", subjects))) as secure:
        path = f"/api/ml/policies/jobs/{queued['id']}/cancel"

        def auth(name: str) -> dict[str, str]:
            return {"Authorization": "Bearer " + tokens[name]}

        assert secure.post(path, headers=auth("viewer")).status_code == 403
        assert secure.post(path, headers=auth("bob")).status_code == 404
        canceled = secure.post(path, headers=auth("alice"))
        assert canceled.status_code == 200, canceled.text
        assert canceled.json()["status"] == "Cancelled"
