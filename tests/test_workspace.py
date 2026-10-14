"""End-to-end API and worker tests for the local data workspace."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sos_platform.api import create_app
from sos_platform.store import Store
from sos_platform.worker import run_once


@pytest.fixture
def workspace(tmp_path: Path):
    store = Store(tmp_path)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        yield client, store


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
    client, _ = workspace
    dataset = upload(client)
    first = client.post(
        "/api/iceberg/import", json={"dataset_id": dataset["id"], "table_name": "sensor_data"}
    )
    assert first.status_code == 201, first.text
    original = first.json()
    second = client.post(
        "/api/iceberg/import", json={"dataset_id": dataset["id"], "table_name": "sensor_data"}
    )
    assert second.status_code == 201, second.text
    assert original["version"] != second.json()["version"]
    assert client.get(f"/api/datasets/{original['id']}/preview").json()["returned_rows"] == 3
    assert client.get(f"/api/datasets/{second.json()['id']}/preview").json()["returned_rows"] == 6
    assert len(client.get("/api/iceberg/sensor_data/snapshots").json()) >= 2
    linked = client.post(
        "/api/datasets/iceberg",
        json={"table_name": "sensor_data", "snapshot_id": original["version"]},
    )
    assert linked.status_code == 201
    assert client.get(f"/api/datasets/{linked.json()['id']}/preview").json()["returned_rows"] == 3
    missing = client.post(
        "/api/datasets/iceberg", json={"table_name": "sensor_data", "snapshot_id": "987654321"}
    )
    assert missing.status_code == 422
