"""Data Workspace의 API·스토리지·Worker 통합 검증."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from sos_platform import worker as worker_module
from sos_platform.api import create_app
from sos_platform.config import Settings
from sos_platform.datasets import save_upload
from sos_platform.object_store import ObjectStore
from sos_platform.store import Store
from sos_platform.worker import run_once


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    """각 시나리오에 독립 PostgreSQL DB를 부여해 실제 트랜잭션을 검증한다."""
    settings = Settings.from_env()
    base = make_url(settings.database_url)
    s3_endpoint = urlsplit(settings.s3_endpoint)
    # 테스트에서 운영 DB·버킷을 생성하거나 삭제하지 않도록 로컬 전용 경계를 강제한다.
    if base.host not in ("localhost", "127.0.0.1", "::1") or s3_endpoint.hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        pytest.fail("Integration tests require loopback PostgreSQL and S3 endpoints")
    name = "sos_test_" + uuid4().hex[:14]
    bucket = "sos-pytest-" + uuid4().hex[:20]
    monkeypatch.setenv("SOS_S3_BUCKET", bucket)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    test_url = base.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("SOS_DATABASE_URL", test_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", test_url)
    command.upgrade(config, "head")
    storage = ObjectStore()
    try:
        storage.client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
    else:
        pytest.fail("Refusing to reuse an existing S3 test bucket")
    storage.client.create_bucket(Bucket=bucket)
    store = Store(tmp_path, database_url=test_url)
    app = create_app(tmp_path)
    try:
        with TestClient(app) as client:
            yield client, store
    finally:
        store.engine.dispose()
        # 이 테스트가 생성한 UUID 버킷만 정리한다. 공유 버킷이나 운영 객체는 다루지 않는다.
        try:
            paginator = storage.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if objects:
                    storage.client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            storage.client.delete_bucket(Bucket=bucket)
        finally:
            admin.dispose()
            admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            admin.dispose()


def upload(client: TestClient) -> dict:
    res = client.post(
        "/api/datasets/files",
        files={"file": ("sensors.csv", b"t,temperature\n1,22\n2,24\n3,25\n", "text/csv")},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_upload_preview_profile_and_scalar(workspace):
    client, _ = workspace
    data = upload(client)
    assert len(client.get("/api/datasets").json()) == 1
    preview = client.get(f"/api/datasets/{data['id']}/preview").json()
    assert preview["returned_rows"] == 3
    assert "temperature" in preview["schema"]
    result = client.get(f"/api/datasets/{data['id']}/profile").json()
    assert result["sampled"] is True
    assert result["row_count"] == 3
    assert "openapi.json" in client.get("/docs").text
    assert client.get("/openapi.json").status_code == 200


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
    assert "simulated object storage failure" in job["error"]
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


def test_iceberg_snapshot_reproducibility(workspace):
    client, store = workspace
    dataset = upload(client)

    def import_snapshot() -> dict:
        job = client.post(
            "/api/iceberg/import",
            json={
                "dataset_id": dataset["id"],
                "table_name": "sensor_data",
            },
        )
        assert job.status_code == 202, job.text
        assert run_once(store)
        completed = client.get(f"/api/jobs/{job.json()['id']}").json()
        assert completed["status"] == "Succeeded", completed
        response = client.get(f"/api/datasets/{completed['result']['dataset_id']}")
        assert response.status_code == 200
        return response.json()

    original = import_snapshot()
    second = import_snapshot()
    assert original["version"] != second["version"]
    from datetime import UTC, datetime, timedelta

    from sos_platform.datasets import iceberg_catalog
    from sos_platform.retention import inspect_tag, tag_name

    catalog = iceberg_catalog(store)
    table = catalog.load_table("default.sensor_data")
    assert (
        inspect_tag(table, name=tag_name(original["id"]), snapshot_id=original["version"])
        == "retained"
    )
    # 태그된 과거 판본은 만료를 실행해도 참조·읽기 가능해야 한다.
    table.maintenance.expire_snapshots().older_than(datetime.now(UTC) + timedelta(days=1)).commit()
    assert client.get(f"/api/datasets/{original['id']}/preview").json()["returned_rows"] == 3
    report = client.get("/api/storage/iceberg-references").json()
    assert any(
        row["dataset_id"] == original["id"] and row["status"] == "retained" for row in report
    )
    assert client.get(f"/api/datasets/{original['id']}/preview").json()["returned_rows"] == 3
    assert client.get(f"/api/datasets/{second['id']}/preview").json()["returned_rows"] == 6
    assert len(client.get("/api/iceberg/sensor_data/snapshots").json()) >= 2
    linked = client.post(
        "/api/datasets/iceberg",
        json={
            "table_name": "sensor_data",
            "snapshot_id": original["version"],
        },
    )
    assert linked.status_code == 201
    assert client.get(f"/api/datasets/{linked.json()['id']}/preview").json()["returned_rows"] == 3
    missing = client.post(
        "/api/datasets/iceberg",
        json={
            "table_name": "sensor_data",
            "snapshot_id": "987654321",
        },
    )
    assert missing.status_code == 422


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


def test_interrupted_iceberg_import_never_retries_implicitly(workspace):
    client, store = workspace
    dataset = upload(client)
    created = client.post(
        "/api/iceberg/import",
        json={"dataset_id": dataset["id"], "table_name": "never_retry"},
    ).json()
    claimed = store.claim()
    assert claimed["id"] == created["id"]
    store.recover()
    recovered = store.get_job(created["id"])
    assert recovered["status"] == "Failed"
    assert "Snapshot" in recovered["error"]
    assert recovered["attempts"][0]["status"] == "Interrupted"
    assert run_once(store) is False


def test_cors_uses_configured_web_port(workspace):
    client, _ = workspace
    response = client.options(
        "/api/datasets",
        headers={
            "Origin": "http://localhost:9601",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:9601"


def test_example_onboarding_is_explicit_and_reuses_dataset(workspace):
    client, _ = workspace
    assert client.get("/api/datasets").json() == []
    examples = client.get("/api/examples").json()
    assert {item["id"] for item in examples} == {"machine-sensors", "data-quality"}
    assert all(item["version"] and item["row_count"] > 0 for item in examples)
    assert client.get("/api/datasets").json() == []
    response = client.post("/api/examples/machine-sensors/use")
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["kind"] == "file"
    assert client.get(f"/api/datasets/{item['id']}/preview?limit=100").json()["returned_rows"] == 96
    again = client.post("/api/examples/machine-sensors/use").json()
    assert again["id"] == item["id"]
    assert len(client.get("/api/datasets").json()) == 1
    assert client.post("/api/examples/missing/use").status_code == 404


def test_api_openapi_response_contracts(workspace):
    client, _ = workspace
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/api/datasets"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert "JobOut" in spec["components"]["schemas"]
    assert "ExampleOut" in spec["components"]["schemas"]


def test_file_storage_failure_is_not_user_validation_error(workspace, monkeypatch):
    client, _ = workspace

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("secret-storage-credential")

    monkeypatch.setattr("sos_platform.datasets.ObjectStore.put", unavailable)
    response = client.post(
        "/api/datasets/files",
        files={"file": ("valid.csv", b"time,value\n1,2\n", "text/csv")},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SOS-STORAGE-001"
    assert "secret-storage-credential" not in response.text


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


def test_running_iceberg_import_refuses_ambiguous_cancellation(workspace):
    client, store = workspace
    dataset = upload(client)
    queued = client.post(
        "/api/iceberg/import", json={"dataset_id": dataset["id"], "table_name": "safe_cancel"}
    ).json()
    claimed = store.claim()
    assert claimed["id"] == queued["id"]
    refused = client.post(f"/api/jobs/{queued['id']}/cancel")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "SOS-ICEBERG-CANCEL-409"
    running = store.get_job(queued["id"])
    assert running["status"] == "Running"
    assert running["cancel_requested"] is False
    # 이전 실행 상태에 취소 플래그가 남아 있어도 외부 커밋 결과는 확정할 수 없다.
    with store.connection() as db:
        db.execute(text("UPDATE jobs SET cancel_requested=true WHERE id=:id"), {"id": queued["id"]})
    store.recover()
    recovered = store.get_job(queued["id"])
    assert recovered["status"] == "Failed"
    assert "Snapshot" in recovered["error"]


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


def test_upload_recovery_preserves_shared_content_addressed_blob(workspace, monkeypatch):
    """DB 실패 시 공유 가능 원본을 삭제하지 않고 안전한 재등록을 허용한다."""
    _client, store = workspace
    content = b"time,value\n1,5\n2,6\n"
    real_register = store.register_dataset

    def database_failure(**_kwargs):
        raise RuntimeError("simulated registration failure")

    monkeypatch.setattr(store, "register_dataset", database_failure)
    with pytest.raises(RuntimeError, match="registration failure"):
        save_upload(store, "replay.csv", content)
    key = "datasets/" + hashlib.sha256(content).hexdigest() + ".csv"
    storage = ObjectStore()
    storage.client.head_object(Bucket=storage.config.s3_bucket, Key=key)
    monkeypatch.setattr(store, "register_dataset", real_register)
    registered = save_upload(store, "replay.csv", content)
    assert registered["location"] == storage.location(key)
    assert any(d["location"] == registered["location"] for d in store.datasets())


def test_transform_publish_failure_preserves_unreferenced_attempt_object(workspace, monkeypatch):
    """실패한 전용 결과는 삭제하지 않고 진단용 게시 실패 이력을 남긴다."""
    client, store = workspace
    dataset = upload(client)
    queued = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    )
    assert queued.status_code == 202
    job_id = queued.json()["id"]
    original_finish = store.finish

    def reject_success(target_id, status, **kwargs):
        if status == "Succeeded":
            raise RuntimeError("simulated DB commit error")
        return original_finish(target_id, status, **kwargs)

    monkeypatch.setattr(store, "finish", reject_success)
    assert run_once(store)
    job = store.get_job(job_id)
    assert job["status"] == "Failed"
    location = ObjectStore().location(f"results/{job_id}/{job['attempts'][0]['id']}.parquet")
    writes = store.object_writes(location)
    assert len(writes) == 1
    assert writes[0]["state"] == "Failed"
    assert all(d["location"] != location for d in store.datasets())
    ObjectStore().client.head_object(
        Bucket=ObjectStore().config.s3_bucket,
        Key=ObjectStore().key(location),
    )


def test_storage_inventory_policy_and_protection(workspace):
    """진단에서는 참조 중 Dataset을 보호하고 기본 삭제 정책을 노출한다."""
    client, store = workspace
    dataset = upload(client)
    res = client.get("/api/storage/policy")
    assert res.status_code == 200
    assert res.json()["automatic_deletion"] is False
    assert res.json()["values"]["grace_days"] >= 7

    inspected = []
    cursor = None
    for _ in range(20):
        parameters = {"cursor": cursor} if cursor else {}
        result = client.get("/api/storage/inspection", params=parameters)
        assert result.status_code == 200, result.text
        page = result.json()
        assert page["deletion_enabled"] is False
        assert page["scope"] in ("datasets/", "results/")
        inspected.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    matches = [item for item in inspected if item["location"] == dataset["location"]]
    assert matches
    assert matches[0]["status"] == "protected"
    assert client.get("/api/storage/inspection", params={"cursor": "?"}).status_code == 422
    assert store.object_writes(dataset["location"])[0]["state"] == "Published"


def test_storage_policy_rejects_unsafe_values(monkeypatch):
    """환경변수로 보호 조건을 완화할 수 없다."""
    from sos_platform.config import StoragePolicy

    for name, value in (
        ("SOS_OBJECT_GRACE_DAYS", "1"),
        ("SOS_OBJECT_OBSERVATION_HOURS", "4"),
        ("SOS_OBJECT_MIN_OBSERVATIONS", "1"),
        ("SOS_OBJECT_PAGE_SIZE", "50000"),
    ):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match="Invalid storage policy"):
            StoragePolicy.from_env()
        monkeypatch.delenv(name)


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


def test_storage_candidate_needs_grace_and_repeated_observation():
    """보존 기간을 충족해도 반복 관측 없이는 정리 후보가 아니다."""
    from datetime import UTC, datetime, timedelta

    from sos_platform.config import StoragePolicy
    from sos_platform.reconciler import classify_object

    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    item = {"modified_at": past, "fingerprint": "generation-a"}
    observation = {"first_seen_at": past, "observation_count": 2, "fingerprint": "generation-a"}
    metadata = {
        "referenced": False,
        "writes": [{"state": "Failed", "updated_at": past}],
        "attempts": {},
        "observation": observation,
    }
    assert classify_object(item, metadata, StoragePolicy())[0] == "candidate"
    metadata["referenced"] = True
    assert classify_object(item, metadata, StoragePolicy())[0] == "protected"
    metadata["referenced"] = False
    observation["observation_count"] = 1
    assert classify_object(item, metadata, StoragePolicy())[0] == "waiting"
    observation["observation_count"] = 2
    observation["fingerprint"] = "earlier-generation"
    assert classify_object(item, metadata, StoragePolicy())[0] == "unknown"
    observation["fingerprint"] = "generation-a"
    metadata["writes"] = [
        {
            "state": "Pending",
            "owner_kind": "upload",
            "owner_id": "abc",
            "updated_at": datetime.now(UTC).isoformat(),
        }
    ]
    assert classify_object(item, metadata, StoragePolicy())[0] == "protected"


def test_recovery_marks_interrupted_attempt_writes_failed(workspace):
    """중단된 Attempt의 Pending 기록은 재시도와 구별되는 실패 기록으로 유지한다."""
    client, store = workspace
    dataset = upload(client)
    queued = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    ).json()
    first = store.claim()
    assert first is not None
    attempt_id = first["attempts"][-1]["id"]
    location = ObjectStore().location(f"results/{queued['id']}/{attempt_id}.parquet")
    write_id = store.begin_object_write(
        location=location, owner_kind="attempt", owner_id=attempt_id
    )
    store.recover()
    writes = store.object_writes(location)
    assert len(writes) == 1
    assert writes[0]["id"] == write_id
    assert writes[0]["state"] == "Failed"
    assert run_once(store)
    result = store.get_job(queued["id"])
    assert result["status"] == "Succeeded"
    registered = store.get_dataset(result["result"]["dataset_id"])
    assert registered is not None
    assert registered["location"] != location


def test_inventory_unknown_object_is_not_candidate():
    """기존 이력 없는 S3 객체를 경과 시간만으로 정리 후보로 분류하지 않는다."""
    from datetime import UTC, datetime, timedelta

    from sos_platform.config import StoragePolicy
    from sos_platform.reconciler import classify_object

    stale = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    item = {"modified_at": stale}
    metadata = {
        "referenced": False,
        "writes": [],
        "attempts": {},
        "observation": {"first_seen_at": stale, "observation_count": 365},
    }
    assert classify_object(item, metadata, StoragePolicy())[0] == "unknown"


def test_object_observation_resets_when_generation_changes(workspace):
    """동일 S3 주소의 다른 세대가 나타나면 기존 관측 증거를 재사용하지 않는다."""
    from datetime import UTC, datetime, timedelta

    _, store = workspace
    location = ObjectStore().location("results/unpublished/example.parquet")
    previous = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    first = {"location": location, "fingerprint": "etag-size-time-a"}
    second = {"location": location, "fingerprint": "etag-size-time-b"}
    store.observe_objects([first], 24)
    with store.connection() as db:
        db.execute(
            text(
                "UPDATE object_observations SET first_seen_at=:old,last_seen_at=:old,"
                "observation_count=2 WHERE location=:location"
            ),
            {"old": previous, "location": location},
        )
    stable = store.observe_objects([first], 24)[location]["observation"]
    assert stable["observation_count"] == 3
    changed = store.observe_objects([second], 24)[location]["observation"]
    assert changed["fingerprint"] == second["fingerprint"]
    assert changed["observation_count"] == 1
    assert changed["first_seen_at"] > previous
    assert store.observe_objects([second], 24)[location]["observation"]["observation_count"] == 1


def test_abandoned_upload_intent_is_never_auto_classified_as_candidate(workspace):
    """완료 신호가 없는 Upload는 오래돼도 소유권이 불명확하므로 보호한다."""
    from datetime import UTC, datetime, timedelta

    from sos_platform.config import StoragePolicy
    from sos_platform.reconciler import classify_object

    _, store = workspace
    location = ObjectStore().location("datasets/abandoned.csv")
    store.begin_object_write(location=location, owner_kind="upload", owner_id=str(uuid4()))
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with store.connection() as db:
        db.execute(
            text("UPDATE object_writes SET updated_at=:old WHERE location=:location"),
            {"old": old, "location": location},
        )
    detail = store.observe_objects([{"location": location, "fingerprint": "stable"}], 24)[location]
    detail["observation"] = {
        "fingerprint": "stable",
        "first_seen_at": old,
        "observation_count": 10,
    }
    result = classify_object({"fingerprint": "stable", "modified_at": old}, detail, StoragePolicy())
    assert result[0] == "unknown"
    assert "삭제하지 않습니다" in result[1]
    assert store.object_writes(location)[0]["state"] == "Pending"


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


def test_upgrade_from_0001_preserves_existing_records(workspace):
    """실제 0001 DB 레코드를 보존하고 0003까지 순서대로 스키마를 올린다."""
    _, store = workspace
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", store.database_url)
    command.downgrade(config, "0001")
    dataset_id = str(uuid4())
    with store.connection() as db:
        db.execute(
            text(
                "INSERT INTO datasets(id,name,kind,location,version,created_at) "
                "VALUES(:id,'legacy.csv','file','s3://sos-data/datasets/legacy.csv','v1',:at)"
            ),
            {"id": dataset_id, "at": "2026-10-01T00:00:00+00:00"},
        )
    command.upgrade(config, "head")
    store.initialize()
    assert store.get_dataset(dataset_id)["name"] == "legacy.csv"
    assert store.get_dataset(dataset_id)["owner_actor"] is None
    with store.connection() as db:
        rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert rev == "0004"
        names = {
            row[0]
            for row in db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='object_observations'"
                )
            )
        }
    assert "fingerprint" in names


def test_identity_roles_ownership_and_audit(workspace, monkeypatch):
    """Bearer 신원·기본 거부·리소스 소유권·변경 감사가 API에서 강제된다."""
    client, store = workspace
    dataset = upload(client)
    from sos_platform.api import create_app

    users = {
        "alice": {"actor_id": "alice", "kind": "human", "roles": ["researcher"]},
        "bob": {"actor_id": "bob", "kind": "human", "roles": ["viewer"]},
        "ops": {"actor_id": "ops", "kind": "human", "roles": ["operator"]},
        "admin": {"actor_id": "admin", "kind": "human", "roles": ["administrator"]},
    }
    tokens = {name: ("sos-secure-" + name + "-" * 40) for name in users}
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(tokens[name].encode()).hexdigest(): user
                for name, user in users.items()
            }
        ),
    )
    with TestClient(create_app(store.home)) as secure:

        def headers(name):
            return {"Authorization": "Bearer " + tokens[name]}

        assert secure.get("/api/datasets").status_code == 401
        assert (
            secure.get("/api/datasets", headers={"Authorization": "Bearer bad"}).status_code == 401
        )
        assert secure.get("/api/auth/me", headers=headers("alice")).json()["actor_id"] == "alice"
        assert secure.get("/api/storage/inspection", headers=headers("alice")).status_code == 403
        assert secure.get("/api/audit", headers=headers("alice")).status_code == 403
        assert secure.get("/api/storage/policy", headers=headers("ops")).status_code == 200
        assert (
            secure.post(
                "/api/jobs",
                json={"dataset_id": dataset["id"], "kind": "profile"},
                headers=headers("alice"),
            ).status_code
            == 404
        )
        result = secure.post(
            "/api/datasets/files",
            files={"file": ("user.csv", b"a,b\n1,2\n", "text/csv")},
            headers=headers("alice"),
        )
        assert result.status_code == 201, result.text
        owned = result.json()["id"]
        assert secure.get(f"/api/datasets/{owned}", headers=headers("bob")).status_code == 404
        assert secure.get(f"/api/datasets/{owned}", headers=headers("alice")).status_code == 200
        assert secure.get("/api/datasets", headers=headers("bob")).json() == []
        assert (
            secure.post(
                "/api/datasets/files",
                files={"file": ("no.csv", b"a\n1\n", "text/csv")},
                headers=headers("bob"),
            ).status_code
            == 403
        )
        job = secure.post(
            "/api/jobs",
            json={"dataset_id": owned, "kind": "profile"},
            headers=headers("alice"),
        )
        assert job.status_code == 202, job.text
        assert (
            secure.get(f"/api/jobs/{job.json()['id']}", headers=headers("bob")).status_code == 404
        )
        assert secure.get("/api/jobs", headers=headers("bob")).json()["items"] == []
        assert (
            secure.post(f"/api/jobs/{job.json()['id']}/cancel", headers=headers("bob")).status_code
            == 403
        )
        cancelled = secure.post(f"/api/jobs/{job.json()['id']}/cancel", headers=headers("alice"))
        assert cancelled.status_code == 200, cancelled.text
        history = secure.get("/api/audit", headers=headers("ops"))
        assert history.status_code == 200
        events = history.json()
        assert any(x["action"] == "dataset.create" and x["actor_id"] == "alice" for x in events)
        assert any(x["action"] == "job.cancel" and x["actor_id"] == "alice" for x in events)
        assert any(x["action"] == "security.access_denied" for x in events)
        assert tokens["alice"] not in json.dumps(events)


def test_protected_mode_rejects_invalid_credentials(monkeypatch):
    """토큰 모드는 누락·잘못된 식별자·무효 역할 설정을 기동 전에 거부한다."""
    from sos_platform.security import IdentitySettings

    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.delenv("SOS_AUTH_TOKEN_HASHES", raising=False)
    with pytest.raises(ValueError):
        IdentitySettings.from_env()
    monkeypatch.setenv("SOS_AUTH_TOKEN_HASHES", '{"bad": {"actor_id": "admin"}}')
    with pytest.raises(ValueError):
        IdentitySettings.from_env()


def test_snapshots_available_without_tag_have_explicit_unprotected_state(workspace):
    """기존 Iceberg Dataset의 보존 미보장 상태와 Operator 전환을 구별한다."""
    client, store = workspace
    source = upload(client)
    job = client.post(
        "/api/iceberg/import", json={"dataset_id": source["id"], "table_name": "legacy_retention"}
    ).json()
    assert run_once(store)
    stored = client.get(f"/api/jobs/{job['id']}").json()
    retained = store.get_dataset(stored["result"]["dataset_id"])
    assert retained is not None
    # 기존 데이터베이스에 등록됐지만 Tag가 없는 Dataset을 실제로 재현한다.
    with store.connection() as db:
        legacy_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO datasets(id,name,kind,location,version,created_at) "
                "VALUES(:id,'legacy','iceberg',:location,:version,:at)"
            ),
            {
                "id": legacy_id,
                "location": retained["location"],
                "version": retained["version"],
                "at": "2026-10-01T00:00:00+00:00",
            },
        )
    previous = client.get("/api/storage/iceberg-references").json()
    assert any(
        x["dataset_id"] == legacy_id and x["status"] == "available_unprotected" for x in previous
    )
    promoted = client.post(f"/api/storage/iceberg-references/{legacy_id}/retain")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "retained"
    assert store.snapshot_retention(legacy_id)["state"] == "Retained"


def test_retained_snapshot_repeated_protection_is_idempotent(workspace):
    """같은 Dataset 보존 확인을 반복해도 유효한 Tag·DB 상태가 유지된다."""
    client, store = workspace
    source = upload(client)
    response = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source["id"], "table_name": "retry_retention"},
    )
    assert response.status_code == 202
    assert run_once(store)
    job = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert job["status"] == "Succeeded"
    dataset_id = job["result"]["dataset_id"]
    for _ in range(2):
        response = client.post(f"/api/storage/iceberg-references/{dataset_id}/retain")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "retained"
    assert store.snapshot_retention(dataset_id)["state"] == "Retained"


def test_required_audit_failure_rolls_back_job_creation(workspace, monkeypatch):
    """중요 변경의 감사 기록을 확정하지 못하면 Job 생성도 커밋되지 않는다."""
    client, store = workspace
    source = upload(client)

    def fail_audit(_db, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(Store, "_audit", staticmethod(fail_audit))
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        store.create_job(
            kind="profile",
            dataset_id=source["id"],
            spec={},
            owner_actor="local-developer",
        )
    assert store.jobs() == []


def test_development_identity_rejects_forwarded_requests(workspace):
    """로컬 프록시로 전달된 외부 요청에 개발용 관리자 권한을 주지 않는다."""
    client, _ = workspace
    response = client.get("/api/datasets", headers={"X-Forwarded-For": "198.51.100.9"})
    assert response.status_code == 403


def test_tag_failure_keeps_uncertain_intent_without_publishing_dataset(workspace, monkeypatch):
    """Catalog Tag 생성 실패 후 보존 의도는 남고 Dataset은 잘못 게시되지 않는다."""
    client, store = workspace
    source = upload(client)
    job = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source["id"], "table_name": "tag_failure"},
    )
    assert job.status_code == 202
    assert run_once(store)
    from pyiceberg.table import Table

    from sos_platform.datasets import connect_iceberg

    before = len(store.datasets())

    def fail_tag(_table):
        raise RuntimeError("Tag commit unavailable")

    monkeypatch.setattr(Table, "manage_snapshots", fail_tag)
    with pytest.raises(RuntimeError, match="Tag commit unavailable"):
        connect_iceberg(store, "tag_failure")
    assert len(store.datasets()) == before
    with store.connection() as db:
        rows = (
            db.execute(
                text("SELECT dataset_id,state FROM snapshot_retention WHERE state='Uncertain'")
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert store.get_dataset(rows[0]["dataset_id"]) is None
    inspection = client.get("/api/storage/iceberg-references")
    assert inspection.status_code == 200
    pending = next(
        item for item in inspection.json() if item["dataset_id"] == rows[0]["dataset_id"]
    )
    assert pending["status"] == "unknown"
    assert "미게시" in pending["reason"]


def test_denied_audit_failure_does_not_grant_access(workspace, monkeypatch):
    """거부 요청의 감사 DB 기록 실패가 접근 허용으로 바뀌지 않는다."""
    _, store = workspace
    from sos_platform.api import create_app

    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(b"secure-secret-" + b"x" * 40).hexdigest(): {
                    "actor_id": "reader",
                    "kind": "human",
                    "roles": ["viewer"],
                }
            }
        ),
    )

    def fail_audit(_store, **_kwargs):
        raise RuntimeError("Audit database unavailable")

    monkeypatch.setattr(Store, "record_audit", fail_audit)
    with TestClient(create_app(store.home)) as client:
        assert client.get("/api/datasets").status_code == 401
        assert client.post("/api/datasets/files").status_code == 401


def test_import_uses_attempt_snapshot_not_latest(workspace, monkeypatch):
    """동일 테이블에 외부 Append가 끼어도 자신의 Attempt Snapshot을 게시한다."""
    client, store = workspace
    source = upload(client)
    from pyiceberg.table import Table

    from sos_platform.datasets import iceberg_catalog, import_to_iceberg

    original_append = Table.append

    def interleaved_append(table, arrow, **kwargs):
        result = original_append(table, arrow, **kwargs)
        original_append(table, arrow, snapshot_properties={"sos.attempt-id": "other-writer"})
        return result

    monkeypatch.setattr(Table, "append", interleaved_append)
    published = import_to_iceberg(store, source, "interleaved_import", str(uuid4()))
    table = iceberg_catalog(store).load_table("default.interleaved_import")
    assert published["version"] != str(table.current_snapshot().snapshot_id)
    assert store.snapshot_retention(published["retention_id"])["state"] == "Pending"
    from sos_platform.retention import inspect_tag, tag_name

    assert (
        inspect_tag(
            table,
            name=tag_name(published["retention_id"]),
            snapshot_id=published["version"],
        )
        == "retained"
    )


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


def test_object_observation_is_batched(workspace):
    """관측 객체 수에 비례한 개별 SQL INSERT가 발생하지 않는다."""
    _, store = workspace
    seen = []

    def capture(_connection, _cursor, statement, _params, _context, executemany):
        if "INSERT INTO object_observations" in statement:
            seen.append(executemany)

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        locations = [ObjectStore().location(f"results/batch-{i}.parquet") for i in range(3)]
        observed = store.observe_objects(
            [{"location": location, "fingerprint": "same"} for location in locations], 24
        )
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    assert len(observed) == 3
    assert seen == [True]


def test_configured_cors_allows_token_preflight(workspace, monkeypatch):
    """설정 포트의 개발용 UI가 Bearer 요청을 보낼 수 있어야 한다."""
    _, store = workspace
    monkeypatch.setenv("SOS_WEB_PORT", "9765")
    with TestClient(create_app(store.home)) as app:
        response = app.options(
            "/api/datasets",
            headers={
                "Origin": "http://localhost:9765",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:9765"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
