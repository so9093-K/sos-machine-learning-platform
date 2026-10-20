"""실제 ML 학습·Scoring·설명·정책·평가의 게시 경계를 한 번의 입력으로 확인한다."""

import hashlib
from uuid import uuid4

import polars as pl
from fastapi.testclient import TestClient
from sqlalchemy import text

from sos_platform.api import create_app
from sos_platform.object_store import ObjectStore
from sos_platform.security import Actor, IdentitySettings
from sos_platform.worker import run_once


def _upload(client, name, content):
    response = client.post(
        "/api/datasets/files",
        files={"file": (name, content, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _submit(client, endpoint, body):
    return client.post(endpoint, json=body, headers={"Idempotency-Key": str(uuid4())})


def test_scoring_explanation_policy_and_evaluation_share_published_artifacts(workspace, tmp_path):
    """하나의 학습·Scoring 결과로 네 개 독립 게시 API의 계약을 확인한다."""
    client, store = workspace
    training = _upload(
        client,
        "ml-train.csv",
        b"temperature,pressure\n1.0,10.0\n2.0,11.0\n3.0,12.0\n4.0,13.0\n5.0,14.0\n",
    )
    trained = _submit(
        client,
        "/api/ml/train/jobs",
        {
            "dataset_id": training["id"],
            "features": ["temperature", "pressure"],
            "params": {"n_estimators": 20},
        },
    )
    assert trained.status_code == 202, trained.text
    assert run_once(store)
    train_job = client.get(f"/api/ml/train/jobs/{trained.json()['id']}").json()
    assert train_job["status"] == "Succeeded", train_job
    model_id = train_job["result"]["model_revision_id"]
    model = client.get(f"/api/ml/models/{model_id}").json()
    assert model["runtime"]["feature_dtypes"] == ["Float64", "Float64"]
    assert len(model["runtime"]["feature_reference_medians"]) == 2

    scoring = _upload(
        client,
        "ml-score.csv",
        b"temperature,pressure,abnormal\n"
        b"1.0,10.0,0\n2.0,11.0,0\n3.0,12.0,0\n200.0,1000.0,1\n",
    )
    spec = {
        "dataset_id": scoring["id"],
        "model_revision_id": model_id,
        "label_column": "abnormal",
    }
    key = str(uuid4())
    first = client.post("/api/ml/scoring/jobs", json=spec, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    again = client.post("/api/ml/scoring/jobs", json=spec, headers={"Idempotency-Key": key})
    assert again.status_code == 202
    assert again.json()["id"] == first.json()["id"]
    score_job_id = first.json()["id"]
    assert client.get(f"/api/jobs/{score_job_id}").status_code == 404
    assert client.post(f"/api/jobs/{score_job_id}/cancel").status_code == 404
    assert run_once(store)
    score_job = client.get(f"/api/ml/scoring/jobs/{score_job_id}").json()
    assert score_job["status"] == "Succeeded", score_job
    scoring_id = score_job["result"]["scoring_result_id"]
    scored = client.get(f"/api/ml/scoring/results/{scoring_id}")
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
    assert frame["features"].struct.field("temperature").to_list() == [1, 2, 3, 200]
    observed = store.observe_objects(
        [{"location": result["location"], "fingerprint": "scoring-generation"}], 24
    )
    assert observed[result["location"]]["referenced"] is True

    rejected = _submit(
        client, "/api/ml/explanations/jobs", {"scoring_result_id": scoring_id, "row_index": 9}
    )
    assert rejected.status_code == 422, rejected.text
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM explanation_results")).scalar_one() == 0
    explain = _submit(
        client, "/api/ml/explanations/jobs", {"scoring_result_id": scoring_id, "row_index": 0}
    )
    assert explain.status_code == 202, explain.text
    assert run_once(store)
    explanation_job = client.get(f"/api/ml/explanations/jobs/{explain.json()['id']}").json()
    assert explanation_job["status"] == "Succeeded", explanation_job
    explanation_id = explanation_job["result"]["explanation_result_id"]
    explanation = client.get(f"/api/ml/explanations/{explanation_id}")
    assert explanation.status_code == 200, explanation.text
    output = explanation.json()["output"]
    assert output["method"] == "median_reference_feature_replacement"
    assert output["additive_attribution"] is False
    assert output["row_index"] == 0
    assert [item["feature"] for item in output["feature_sensitivities"]] == [
        "temperature",
        "pressure",
    ]
    assert all(
        isinstance(item["anomaly_score_delta"], float)
        for item in output["feature_sensitivities"]
    )

    policy_job = _submit(
        client,
        "/api/ml/policies/jobs",
        {"scoring_result_id": scoring_id, "mode": "fixed", "threshold": 0.5},
    )
    assert policy_job.status_code == 202, policy_job.text
    assert client.post(f"/api/jobs/{policy_job.json()['id']}/cancel").status_code == 404
    assert run_once(store)
    policy_finished = client.get(f"/api/ml/policies/jobs/{policy_job.json()['id']}").json()
    assert policy_finished["status"] == "Succeeded", policy_finished
    policy = client.get(
        f"/api/ml/policies/{policy_finished['result']['policy_revision_id']}"
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["threshold"] == 0.5
    policy_id = policy.json()["id"]
    assert model == client.get(f"/api/ml/models/{model_id}").json()

    evaluation_job = _submit(
        client,
        "/api/ml/evaluations/jobs",
        {"scoring_result_id": scoring_id, "policy_revision_id": policy_id},
    )
    assert evaluation_job.status_code == 202, evaluation_job.text
    assert client.post(f"/api/jobs/{evaluation_job.json()['id']}/cancel").status_code == 404
    assert run_once(store)
    evaluated = client.get(
        f"/api/ml/evaluations/jobs/{evaluation_job.json()['id']}"
    ).json()
    assert evaluated["status"] == "Succeeded", evaluated
    evaluation = client.get(
        f"/api/ml/evaluations/{evaluated['result']['evaluation_revision_id']}"
    )
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["label_coverage"] == "full"
    assert evaluation.json()["decision_metrics"]["precision"] is not None
    assert evaluation.json()["ranking_metrics"]["average_precision"] is not None

    with store.connection() as db:
        for table in (
            "model_revisions",
            "scoring_results",
            "explanation_results",
            "decision_policy_revisions",
            "evaluation_revisions",
        ):
            assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 1
        assert (
            db.execute(
                text("SELECT COUNT(*) FROM datasets WHERE id=:id"), {"id": scoring["id"]}
            ).scalar_one()
            == 1
        )

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
    """미실행 ML Job의 종류별 취소 라우팅과 결과 미게시를 확인한다."""
    client, store = workspace
    dataset = _upload(client, "cancel-ml.csv", b"temperature\n1.0\n2.0\n")
    cases = (
        ("policies", "ml_policy"),
        ("evaluations", "ml_evaluate"),
        ("explanations", "ml_explain"),
    )
    for route, kind in cases:
        queued = store.create_job(
            kind=kind,
            dataset_id=dataset["id"],
            spec={"scoring_result_id": "not-executed"},
            owner_actor="local-developer",
            actor_type="human",
        )
        job_id = queued["id"]
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404
        other = next(name for name, _kind in cases if name != route)
        assert client.post(f"/api/ml/{other}/jobs/{job_id}/cancel").status_code == 404
        canceled = client.post(f"/api/ml/{route}/jobs/{job_id}/cancel")
        assert canceled.status_code == 200, canceled.text
        assert canceled.json()["status"] == "Cancelled"
        assert canceled.json()["result"] is None
        assert client.get(f"/api/ml/{route}/jobs/{job_id}").json()["status"] == "Cancelled"
        assert client.post(f"/api/ml/{route}/jobs/{job_id}/cancel").status_code == 200
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
