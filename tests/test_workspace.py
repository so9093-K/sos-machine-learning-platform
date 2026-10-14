"""End-to-end API and worker tests for the local data workspace."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from sos_platform.config import Settings
from sos_platform.object_store import ObjectStore
from fastapi.testclient import TestClient

from sos_platform.api import create_app
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
    assert Path(completed["result"]["artifact"]).is_file()
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
        job = client.post("/api/iceberg/import", json={
            "dataset_id": dataset["id"], "table_name": "sensor_data",
        })
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
    linked = client.post("/api/datasets/iceberg", json={
        "table_name": "sensor_data", "snapshot_id": original["version"],
    })
    assert linked.status_code == 201
    assert client.get(f"/api/datasets/{linked.json()['id']}/preview").json()[
        "returned_rows"
    ] == 3
    missing = client.post("/api/datasets/iceberg", json={
        "table_name": "sensor_data", "snapshot_id": "987654321",
    })
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
