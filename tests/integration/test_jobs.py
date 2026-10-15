"""Job·Feature·Attempt 실행 통합 계약을 검증한다."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from sos_platform import worker as worker_module
from sos_platform.object_store import ObjectStore
from sos_platform.store import Store
from sos_platform.worker import run_once

from .support import upload


def test_safe_feature_preview_and_worker_job(workspace):
    client, store = workspace
    dataset = upload(client)
    transform = {
        "order_by": "t",
        "steps": [
            {"op": "fill_null", "column": "temperature", "value": 0},
            {"op": "lag", "column": "temperature", "periods": 1, "alias": "previous"},
        ],
    }
    body = {"dataset_id": dataset["id"], "transform": transform}
    preview = client.post("/api/features/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert "previous" in preview.json()["schema"]
    queued = client.post("/api/jobs", json={**body, "kind": "transform"})
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["id"]
    assert queued.json()["status"] == "Queued"
    assert run_once(store) is True
    completed = client.get(f"/api/jobs/{job_id}").json()
    assert completed["status"] == "Succeeded", completed
    materialized = client.get(f"/api/datasets/{completed['result']['dataset_id']}")
    assert materialized.status_code == 200
    assert (
        client.get(f"/api/datasets/{completed['result']['dataset_id']}/preview").json()[
            "returned_rows"
        ]
        == 3
    )
    assert completed["attempts"][0]["status"] == "Succeeded"
    assert run_once(store) is False


def test_failed_result_publish_cleans_local_worker_files(workspace, monkeypatch):
    """객체 저장소 게시 실패 시 영속 원본을 유지하고 임시 산출물만 지운다."""
    client, store = workspace
    dataset = upload(client)
    response = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    def fail_upload(_store, _key, source):
        assert source.is_file()
        raise RuntimeError("simulated object storage failure")

    monkeypatch.setattr(ObjectStore, "put", fail_upload)
    assert run_once(store) is True
    job = store.get_job(job_id)
    assert job["status"] == "Failed"
    assert job["error_code"] == "SOS-JOB-FAILED"
    assert job["failure_stage"] == "publication"
    assert job["failure_category"] == "unclassified"
    assert job["exception_type"] == "RuntimeError"
    assert job["attempts"][0]["failure_category"] == "unclassified"
    assert "simulated object storage failure" not in job["error"]
    results = store.home / "results"
    assert not (results / f"{job_id}.parquet").exists()
    assert not (results / f"{job_id}.pending.parquet").exists()
    assert not (results / f"{job_id}.completed.json").exists()
    assert store.get_dataset(dataset["id"]) is not None


def test_cancel_recovery_and_input_validation(workspace):
    client, store = workspace
    dataset = upload(client)
    cancelled = client.post(
        "/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}
    ).json()
    result = client.post(f"/api/jobs/{cancelled['id']}/cancel").json()
    assert result["status"] == "Cancelled"
    assert run_once(store) is False
    queued = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}).json()
    running = store.claim()
    assert running["id"] == queued["id"]
    store.recover()
    job = client.get(f"/api/jobs/{queued['id']}").json()
    assert job["status"] == "Queued"
    assert job["attempts"][0]["status"] == "Interrupted"
    assert run_once(store)
    assert client.get(f"/api/jobs/{queued['id']}").json()["status"] == "Succeeded"
    assert client.get("/api/datasets/unknown").status_code == 404
    assert (
        client.post(
            "/api/datasets/files", files={"file": ("bad.exe", b"x", "text/plain")}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/features/preview",
            json={
                "dataset_id": dataset["id"],
                "transform": {"steps": [{"op": "lag", "column": "temperature"}]},
            },
        ).status_code
        == 422
    )


def test_cancel_running_execution_and_do_not_publish(workspace):
    client, store = workspace
    dataset = upload(client)
    created = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_once, store)
        for _ in range(200):
            if store.get_job(created["id"])["status"] == "Running":
                break
            time.sleep(0.01)
        assert store.get_job(created["id"])["status"] == "Running"
        requested = client.post(f"/api/jobs/{created['id']}/cancel").json()
        assert requested["cancel_requested"] is True
        assert future.result(timeout=30) is True
    completed = client.get(f"/api/jobs/{created['id']}").json()
    assert completed["status"] == "Cancelled"
    assert completed["result"] is None
    assert completed["attempts"][0]["status"] == "Cancelled"


def test_job_list_preserves_attempt_history(workspace):
    client, store = workspace
    dataset = upload(client)
    ids = [
        client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}).json()["id"]
        for _ in range(3)
    ]
    store.claim()
    store.recover()
    listed = {item["id"]: item for item in client.get("/api/jobs").json()["items"]}
    assert set(ids).issubset(listed)
    assert listed[ids[0]]["attempt_count"] == 1
    assert listed[ids[0]]["latest_attempt"]["status"] == "Interrupted"
    assert "attempts" not in listed[ids[0]]
    detail = client.get(f"/api/jobs/{ids[0]}").json()
    assert len(detail["attempts"]) == 1


def test_job_listing_uses_bounded_number_of_queries(workspace):
    client, store = workspace
    dataset = upload(client)
    for _ in range(5):
        client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    queries = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        assert len(store.jobs()) == 5
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    assert len(queries) == 2


@pytest.mark.parametrize("explicit_home", [False, True])
def test_worker_home_matches_configuration_and_explicit_override(
    monkeypatch, tmp_path, explicit_home
):
    """Worker 로컬 경로가 환경 설정을 따르며 CLI 인자가 우선한다."""
    from sys import argv

    seen = {}

    class FakeStore:
        def __init__(self, home):
            seen["home"] = home

        def initialize(self):
            pass

        def acquire_worker(self):
            return True

        def recover(self):
            pass

        def heartbeat(self):
            pass

        def release_worker(self):
            pass

    env_home = tmp_path / "env-home"
    cli_home = tmp_path / "cli-home"
    monkeypatch.setenv("SOS_HOME", str(env_home))
    monkeypatch.setattr(worker_module, "Store", FakeStore)
    monkeypatch.setattr(worker_module, "run_once", lambda _store: False)
    command = [argv[0], "--once"]
    if explicit_home:
        command.extend(["--home", str(cli_home)])
    monkeypatch.setattr("sys.argv", command)

    worker_module.main()
    assert seen["home"] == (cli_home if explicit_home else env_home)


def test_job_cursor_pagination_has_no_duplicates_or_missing_items(workspace):
    client, store = workspace
    dataset = upload(client)
    ids = [
        client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}).json()["id"]
        for _ in range(43)
    ]
    seen = []
    cursor = None
    while True:
        query = {"limit": 10}
        if cursor is not None:
            query["cursor"] = cursor
        response = client.get("/api/jobs", params=query)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) <= 10
        seen.extend(job["id"] for job in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(ids)
    assert set(seen) == set(ids)
    assert len(set(seen)) == len(seen)
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422
    assert client.get("/api/jobs", params={"limit": 101}).status_code == 422
    assert client.get("/api/jobs", params={"cursor": "invalid"}).status_code == 422
    assert len(store.jobs()) == 20


def test_immutable_attempt_results_across_worker_recovery(workspace):
    """재실행한 Attempt는 이전 결과 객체 경로를 재사용하지 않는다."""
    client, store = workspace
    dataset = upload(client)
    posted = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    )
    assert posted.status_code == 202
    job_id = posted.json()["id"]
    first = store.claim()
    assert first is not None
    first_attempt = first["attempts"][0]["id"]
    store.recover()
    assert run_once(store)
    job = store.get_job(job_id)
    assert job["status"] == "Succeeded"
    assert len(job["attempts"]) == 2
    assert job["attempts"][-1]["id"] != first_attempt
    result = store.get_dataset(job["result"]["dataset_id"])
    assert result is not None
    assert result["location"].endswith(f"/{job['attempts'][-1]['id']}.parquet")


def test_job_summary_bounds_attempt_payload_and_preserves_detail(workspace):
    """Job 목록은 수백 번의 Attempt가 있더라도 요약만 전송한다."""
    client, store = workspace
    dataset = upload(client)
    job = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}).json()
    with store.connection() as db:
        for number in range(1, 151):
            db.execute(
                text(
                    "INSERT INTO attempts(id,job_id,number,status,started_at,finished_at) "
                    "VALUES(:id,:job,:number,'Interrupted',:at,:at)"
                ),
                {
                    "id": str(uuid4()),
                    "job": job["id"],
                    "number": number,
                    "at": "2026-10-01T00:00:00+00:00",
                },
            )
    response = client.get("/api/jobs?limit=1")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["attempt_count"] == 150
    assert item["latest_attempt"]["number"] == 150
    assert "attempts" not in item
    assert len(client.get(f"/api/jobs/{job['id']}").json()["attempts"]) == 150


def test_worker_completion_audit_and_atomic_failure(workspace, monkeypatch):
    """Worker 완료·결과 Dataset의 감사를 연결하고 감사 실패 시 DB 변경을 롤백한다."""
    client, store = workspace
    source = upload(client)
    first = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    assert first.status_code == 202
    assert run_once(store)
    finished = store.get_job(first.json()["id"])
    attempt_id = finished["attempts"][-1]["id"]
    events = store.audit_events(limit=100)
    assert any(
        event["action"] == "job.finish"
        and event["resource_id"] == finished["id"]
        and attempt_id in event["reason"]
        for event in events
    )

    second = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    job_id = second.json()["id"]
    assert store.claim()["id"] == job_id
    before = len(store.datasets())
    original_audit = Store._audit

    def fail_finished_audit(db, **kwargs):
        if kwargs["action"] == "job.finish":
            raise RuntimeError("Required completion audit unavailable")
        return original_audit(db, **kwargs)

    monkeypatch.setattr(Store, "_audit", staticmethod(fail_finished_audit))
    with pytest.raises(RuntimeError, match="Required completion audit unavailable"):
        store.finish(
            job_id,
            "Succeeded",
            publish={
                "name": "never-committed.parquet",
                "location": ObjectStore().location("results/no-publish.parquet"),
                "version": "unpublished",
            },
        )
    assert store.get_job(job_id)["status"] == "Running"
    assert len(store.datasets()) == before


def test_failed_job_completion_is_audited(workspace):
    """Worker 실패 기록은 Job 상태와 Attempt 식별자를 하나의 트랜잭션에 확정한다."""
    client, store = workspace
    source = upload(client)
    created = client.post("/api/jobs", json={"dataset_id": source["id"], "kind": "profile"})
    assert created.status_code == 202
    claimed = store.claim()
    assert claimed["id"] == created.json()["id"]
    attempt_id = claimed["attempts"][-1]["id"]
    store.finish(claimed["id"], "Failed", error="test failure")
    failed = store.get_job(claimed["id"])
    assert failed["status"] == "Failed"
    assert any(
        entry["action"] == "job.finish"
        and entry["outcome"] == "failed"
        and entry["resource_id"] == claimed["id"]
        and attempt_id in entry["reason"]
        for entry in store.audit_events(limit=100)
    )


def test_feature_preview_uses_full_input_before_grouped_time_windows(workspace):
    client, store = workspace
    # 처음 1,000행 밖에 가장 오래된 측정값을 배치해 샘플 경계를 검증한다.
    rows = ["machine,time,value"]
    rows.extend(f"A,{tick},{tick * 2}" for tick in range(2, 1002))
    rows.append("A,1,2")
    response = client.post(
        "/api/datasets/files",
        files={"file": ("unordered.csv", ("\n".join(rows) + "\n").encode(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    spec = {
        "group_by": ["machine"],
        "order_by": "time",
        "steps": [{"op": "lag", "column": "value", "alias": "previous"}],
    }
    preview = client.post(
        "/api/features/preview", json={"dataset_id": dataset["id"], "transform": spec}
    )
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["sampled"] is False
    assert result["rows"][0]["time"] == 1
    assert result["rows"][0]["previous"] is None
    assert result["rows"][1]["previous"] == 2
