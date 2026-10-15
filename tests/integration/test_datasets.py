"""Dataset 등록·원본·마이그레이션 통합 계약을 검증한다."""

import hashlib
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from sos_platform.datasets import save_upload
from sos_platform.object_store import ObjectStore

from .support import upload


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
        assert rev == "0005"
        # 기존 0001 레코드도 신규 진단 필드를 NULL로 가진 채 보존한다.
        cols = {row[0] for row in db.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='jobs'"
        ))}
        assert {"request_id", "error_code", "failure_stage"} <= cols
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
