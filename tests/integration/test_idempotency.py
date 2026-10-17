"""중복 Job 제출·응답 유실·DB 경합의 일관성을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from sos_platform.store import Store

from .support import upload


def test_replayed_submission_and_conflicting_payload(workspace):
    client, store = workspace
    dataset = upload(client)
    key = str(uuid4())
    body = {"dataset_id": dataset["id"], "kind": "profile"}
    first = client.post("/api/jobs", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    replay = client.post("/api/jobs", json=body, headers={"Idempotency-Key": key})
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM job_submissions")).scalar_one() == 1
    different = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "filter_not_null", "column": "t"}]},
        },
        headers={"Idempotency-Key": key},
    )
    assert different.status_code == 409, different.text
    assert different.json()["code"] == "SOS-JOB-IDEMPOTENCY-CONFLICT"
    assert different.json()["retry_policy"] == "never"


def test_generic_and_exploration_jobs_share_idempotency_namespace(workspace):
    """서로 다른 Job API를 같은 키로 재전송해도 다른 작업이 생성되지 않는다."""
    client, store = workspace
    dataset = upload(client)
    key = str(uuid4())
    general = client.post(
        "/api/jobs",
        json={"dataset_id": dataset["id"], "kind": "profile"},
        headers={"Idempotency-Key": key},
    )
    assert general.status_code == 202, general.text
    exploration_body = {
        "dataset_id": dataset["id"],
        "exploration": {"column": "temperature", "metric": "mean"},
    }
    conflict = client.post(
        "/api/explorations/jobs",
        json=exploration_body,
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "SOS-JOB-IDEMPOTENCY-CONFLICT"

    exploration_key = str(uuid4())
    headers = {"Idempotency-Key": exploration_key}
    first = client.post("/api/explorations/jobs", json=exploration_body, headers=headers)
    assert first.status_code == 202, first.text
    replay = client.post("/api/explorations/jobs", json=exploration_body, headers=headers)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["id"] != general.json()["id"]

    assert client.get(f"/api/jobs/{first.json()['id']}").status_code == 404
    assert client.get(f"/api/explorations/jobs/{general.json()['id']}").status_code == 404
    assert client.post(f"/api/jobs/{first.json()['id']}/cancel").status_code == 404
    assert client.post(
        f"/api/explorations/jobs/{general.json()['id']}/cancel"
    ).status_code == 404
    assert [job["id"] for job in client.get("/api/jobs").json()["items"]] == [
        general.json()["id"]
    ]
    assert [job["id"] for job in client.get("/api/explorations/jobs").json()["items"]] == [
        first.json()["id"]
    ]
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == 2
        assert db.execute(text("SELECT COUNT(*) FROM job_submissions")).scalar_one() == 2


def test_postgres_serializes_concurrent_submissions(workspace):
    client, store = workspace
    dataset = upload(client)
    key = str(uuid4())
    spec = {"dataset_id": dataset["id"], "kind": "profile"}

    def submit(_number):
        return store.create_job(
            kind="profile",
            dataset_id=dataset["id"],
            spec=spec,
            owner_actor="local-developer",
            idempotency_key=key,
        )["id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(20)))
    assert len(set(ids)) == 1
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM job_submissions")).scalar_one() == 1
        assert (
            db.execute(
                text("SELECT COUNT(*) FROM audit_events WHERE action='job.create'")
            ).scalar_one()
            == 1
        )


def test_submission_scope_cancellation_and_validation(workspace):
    client, store = workspace
    dataset = upload(client)
    spec = {"dataset_id": dataset["id"], "kind": "profile"}
    key = str(uuid4())
    actor_a = store.create_job(
        kind="profile",
        dataset_id=dataset["id"],
        spec=spec,
        owner_actor="actor-a",
        idempotency_key=key,
    )
    actor_b = store.create_job(
        kind="profile",
        dataset_id=dataset["id"],
        spec=spec,
        owner_actor="actor-b",
        idempotency_key=key,
    )
    assert actor_a["id"] != actor_b["id"]
    assert (
        client.post("/api/jobs", json=spec, headers={"Idempotency-Key": "short"}).status_code == 422
    )
    first = client.post("/api/jobs", json=spec, headers={"Idempotency-Key": key}).json()
    assert client.post(f"/api/jobs/{first['id']}/cancel").json()["status"] == "Cancelled"
    replay = client.post("/api/jobs", json=spec, headers={"Idempotency-Key": key})
    assert replay.status_code == 202
    assert replay.json()["id"] == first["id"]
    assert replay.json()["status"] == "Cancelled"
    assert store.get_job(first["id"])["attempts"] == []
    conflict = client.post(
        "/api/iceberg/import",
        json={"dataset_id": dataset["id"], "table_name": "another"},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409


def test_failed_transaction_does_not_reserve_submission_key(workspace, monkeypatch):
    client, store = workspace
    dataset = upload(client)
    key = str(uuid4())
    spec = {"dataset_id": dataset["id"], "kind": "profile"}

    def reject_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(Store, "_audit", staticmethod(reject_audit))
        with pytest.raises(RuntimeError, match="audit storage"):
            store.create_job(
                kind="profile",
                dataset_id=dataset["id"],
                spec=spec,
                owner_actor="local-developer",
                idempotency_key=key,
            )
    created = store.create_job(
        kind="profile",
        dataset_id=dataset["id"],
        spec=spec,
        owner_actor="local-developer",
        idempotency_key=key,
    )
    assert created["status"] == "Queued"
    with store.connection() as db:
        assert db.execute(text("SELECT COUNT(*) FROM job_submissions")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM jobs")).scalar_one() == 1
