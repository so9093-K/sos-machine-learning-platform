"""Dataset 등록·원본·마이그레이션 통합 계약을 검증한다."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from sos_platform.api import create_app
from sos_platform.datasets import save_upload
from sos_platform.errors import ERRORS
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


def test_upload_storage_wait_does_not_block_other_api_requests(workspace, monkeypatch):
    """객체 저장소가 느려도 API 이벤트 루프의 별도 조회는 진행되어야 한다."""
    client, _ = workspace
    entered = Event()
    release = Event()
    real_put = ObjectStore.put

    def wait_before_put(self, key, path):
        entered.set()
        if not release.wait(timeout=20):
            raise TimeoutError("Test storage gate was not released")
        return real_put(self, key, path)

    monkeypatch.setattr(ObjectStore, "put", wait_before_put)
    with ThreadPoolExecutor(max_workers=2) as executor:
        upload_future = executor.submit(
            client.post,
            "/api/datasets/files",
            files={"file": ("slow.csv", b"time,value\n1,2\n", "text/csv")},
        )
        try:
            assert entered.wait(timeout=10), "Upload did not reach S3"
            health_future = executor.submit(client.get, "/api/health")
            assert health_future.result(timeout=3).status_code == 200
        finally:
            release.set()
        response = upload_future.result(timeout=20)
        assert response.status_code == 201, response.text


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
        assert rev == "0006"
        # 기존 0001 레코드도 신규 진단 필드를 NULL로 가진 채 보존한다.
        cols = {
            row[0]
            for row in db.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='jobs'")
            )
        }
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


def test_upload_policy_and_raw_request_body_limit(workspace, monkeypatch):
    _client, store = workspace
    monkeypatch.setenv("SOS_MAX_UPLOAD_MIB", "1")
    with TestClient(create_app(store.home)) as client:
        policy = client.get("/api/datasets/upload-policy")
        assert policy.status_code == 200
        assert policy.json()["max_size_bytes"] == 1024 * 1024
        rejected = client.post(
            "/api/datasets/files",
            files={"file": ("huge.csv", b"a" * (1024 * 1024 + 1), "text/csv")},
        )
        assert rejected.status_code == 413
        assert rejected.json()["code"] == "SOS-UPLOAD-TOO-LARGE"
        assert rejected.json()["detail"] == ERRORS["SOS-UPLOAD-TOO-LARGE"].message
        assert rejected.headers["X-Request-ID"] == rejected.json()["request_id"]
        # 본문 자체가 너무 큰 경우 라우트 진입 이전의 제한도 동일한 구조를 사용한다.
        rejected_body = client.post(
            "/api/datasets/files",
            files={"file": ("huge.csv", b"a" * (1024 * 1024 + 80 * 1024), "text/csv")},
        )
        assert rejected_body.status_code == 413
        assert rejected_body.json()["code"] == "SOS-UPLOAD-TOO-LARGE"
        assert rejected_body.headers["X-Request-ID"] == rejected_body.json()["request_id"]

        # Content-Length가 없는 스트리밍 요청도 실제 수신 바이트 수로 제한한다.
        boundary = "sos-upload-boundary"
        head = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="huge.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        streamed = client.post(
            "/api/datasets/files",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            content=iter((head, b"a" * (1024 * 1024 + 80 * 1024), tail)),
        )
        assert streamed.status_code == 413
        assert streamed.json()["code"] == "SOS-UPLOAD-TOO-LARGE"
        assert streamed.headers["X-Request-ID"] == streamed.json()["request_id"]
    assert store.datasets() == []


def test_invalid_format_and_empty_upload_are_user_errors(workspace):
    client, store = workspace
    for name, data, code in (
        ("unsupported.json", b"{}", "SOS-UPLOAD-FORMAT"),
        ("empty.csv", b"", "SOS-UPLOAD-INVALID"),
        ("broken.parquet", b"not a parquet file", "SOS-UPLOAD-INVALID"),
    ):
        response = client.post(
            "/api/datasets/files",
            files={"file": (name, data, "application/octet-stream")},
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == code
        assert response.json()["detail"] == ERRORS[code].message
    assert store.datasets() == []


def test_large_parquet_registers_and_supports_parallel_first_reads(workspace, tmp_path):
    """기존 크기 제한을 넘는 Parquet를 등록하고 Preview·Profile을 함께 읽는다."""
    client, _store = workspace
    path = tmp_path / "large.parquet"
    column = pa.array(range(262_144), type=pa.int64())
    batch = pa.table({"time": column, "signal": column})
    with pq.ParquetWriter(path, batch.schema, compression="NONE", use_dictionary=False) as writer:
        for _ in range(20):
            writer.write_table(batch)
    assert 75 * 1024 * 1024 < path.stat().st_size < 256 * 1024 * 1024
    with path.open("rb") as handle:
        response = client.post(
            "/api/datasets/files",
            files={"file": (path.name, handle, "application/octet-stream")},
        )
    assert response.status_code == 201, response.text[:400]
    dataset = response.json()
    with path.open("rb") as handle:
        assert dataset["version"] == hashlib.file_digest(handle, "sha256").hexdigest()
    with ThreadPoolExecutor(max_workers=2) as executor:
        preview_future = executor.submit(
            client.get, f"/api/datasets/{dataset['id']}/preview?limit=3"
        )
        profile_future = executor.submit(client.get, f"/api/datasets/{dataset['id']}/profile")
        preview = preview_future.result(timeout=60)
        profile = profile_future.result(timeout=60)
    assert preview.status_code == 200, preview.text[:400]
    assert preview.json()["returned_rows"] == 3
    assert profile.status_code == 200, profile.text[:400]
    assert profile.json()["row_count"] == 1000


def test_corrupt_download_cannot_overwrite_valid_cache(workspace, tmp_path, monkeypatch):
    _client, _store = workspace
    destination = tmp_path / "cached.csv"
    destination.write_bytes(b"verified")
    expected = hashlib.sha256(b"verified").hexdigest()

    def bad_download(_bucket, _key, target):
        from pathlib import Path

        Path(target).write_bytes(b"corrupt")

    storage = ObjectStore()
    monkeypatch.setattr(storage.client, "download_file", bad_download)
    with pytest.raises(ValueError, match="hash"):
        storage.get("datasets/file.csv", destination, expected_sha256=expected)
    assert destination.read_bytes() == b"verified"
    assert list(tmp_path.glob("cached.csv.pending-*")) == []


def test_concurrent_uploads_publish_independent_versions_without_staging_leftovers(workspace):
    client, store = workspace
    first = b"signal\n" + b"1\n" * 160_000
    second = b"signal\n" + b"2\n" * 160_000
    staged_before = set((store.home / "cache").glob("tmp*.csv"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [
            executor.submit(
                client.post,
                "/api/datasets/files",
                files={"file": (name, content, "text/csv")},
            )
            for name, content in (("first.csv", first), ("second.csv", second))
        ]
        responses = [attempt.result(timeout=30) for attempt in attempts]
    assert all(response.status_code == 201 for response in responses)
    assert {response.json()["version"] for response in responses} == {
        hashlib.sha256(first).hexdigest(),
        hashlib.sha256(second).hexdigest(),
    }
    assert set((store.home / "cache").glob("tmp*.csv")) == staged_before
    assert len(store.datasets()) == 2
