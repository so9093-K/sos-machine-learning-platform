"""Data Workspace의 API·스토리지·Worker 통합 검증."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from sos_platform import worker as worker_module
from sos_platform.api import create_app
from sos_platform.config import Settings
from sos_platform.object_store import ObjectStore
from sos_platform.store import Store
from sos_platform.worker import run_once


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch):
    """각 시나리오에 독립 PostgreSQL DB를 부여해 실제 트랜잭션을 검증한다."""
    base = make_url(Settings.from_env().database_url)
    name = "sos_test_" + uuid4().hex[:14]
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    test_url = base.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("SOS_DATABASE_URL", test_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", test_url)
    command.upgrade(config, "head")
    ObjectStore().ensure_bucket()
    store = Store(tmp_path, database_url=test_url)
    app = create_app(tmp_path)
    try:
        with TestClient(app) as client:
            yield client, store
    finally:
        store.engine.dispose()
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
            "transform": {
                "steps": [{"op": "fill_null", "column": "temperature", "value": 0}]
            },
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
    store.recover()
    assert store.get_job(queued["id"])["status"] == "Failed"


def test_job_list_preserves_attempt_history(workspace):
    client, store = workspace
    dataset = upload(client)
    ids = [
        client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"}).json()["id"]
        for _ in range(3)
    ]
    store.claim()
    store.recover()
    listed = {item["id"]: item for item in client.get("/api/jobs").json()}
    assert set(ids).issubset(listed)
    assert len(listed[ids[0]]["attempts"]) == 1
    assert listed[ids[0]]["attempts"][0]["status"] == "Interrupted"


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
