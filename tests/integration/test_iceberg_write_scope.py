"""Iceberg 테이블에 대한 쓰기 소유권은 Dataset Snapshot 읽기와 독립적이다."""

import hashlib
import json

import pyarrow as pa
from fastapi.testclient import TestClient

from sos_platform.api import create_app
from sos_platform.datasets import iceberg_catalog
from sos_platform.worker import run_once


def test_iceberg_append_requires_actual_table_owner(workspace, monkeypatch):
    _client, store = workspace
    tokens = {"alice": "secure-alice-" * 8, "bob": "secure-bob-" * 8}
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(value.encode()).hexdigest(): {
                    "actor_id": name,
                    "kind": "human",
                    "roles": ["researcher"],
                }
                for name, value in tokens.items()
            }
        ),
    )

    def headers(who: str, key: str | None = None) -> dict:
        result = {"Authorization": "Bearer " + tokens[who]}
        if key is not None:
            result["Idempotency-Key"] = key
        return result

    with TestClient(create_app(store.home)) as api:
        alice = api.post(
            "/api/datasets/files",
            files={"file": ("alice.csv", b"v\n1\n", "text/csv")},
            headers=headers("alice"),
        )
        bob = api.post(
            "/api/datasets/files",
            files={"file": ("bob.csv", b"v\n2\n", "text/csv")},
            headers=headers("bob"),
        )
        assert alice.status_code == bob.status_code == 201
        alice_id, bob_id = alice.json()["id"], bob.json()["id"]
        first = api.post(
            "/api/iceberg/import",
            json={"dataset_id": alice_id, "table_name": "alice_table"},
            headers=headers("alice", "alice-write-001"),
        )
        assert first.status_code == 202, first.text
        # 최초 Job 실행 전이라도 동일 이름을 다른 사용자가 예약할 수 없어야 한다.
        denied = api.post(
            "/api/iceberg/import",
            json={"dataset_id": bob_id, "table_name": "alice_table"},
            headers=headers("bob", "bob-write-001"),
        )
        assert denied.status_code == 404
        assert run_once(store)
        assert store.get_job(first.json()["id"])["status"] == "Succeeded"
        table = iceberg_catalog(store).load_table("default.alice_table")
        assert len(table.snapshots()) == 1
        # Snapshot을 연결할 권한이 생겨도 테이블 쓰기 소유권은 이전되지 않는다.
        store.register_dataset(
            name="shared-reference",
            kind="iceberg",
            location="default.alice_table",
            version=str(table.current_snapshot().snapshot_id),
            owner_actor="bob",
        )
        assert (
            api.get("/api/iceberg/alice_table/snapshots", headers=headers("bob")).status_code == 200
        )
        denied = api.post(
            "/api/iceberg/import",
            json={"dataset_id": bob_id, "table_name": "alice_table"},
            headers=headers("bob", "bob-write-002"),
        )
        assert denied.status_code == 404
        second = api.post(
            "/api/iceberg/import",
            json={"dataset_id": alice_id, "table_name": "alice_table"},
            headers=headers("alice", "alice-write-002"),
        )
        assert second.status_code == 202, second.text
        assert run_once(store)
        assert store.get_job(second.json()["id"])["status"] == "Succeeded"
        assert len(iceberg_catalog(store).load_table("default.alice_table").snapshots()) == 2

        # API를 우회해 만들어진 Job도 Worker에서 커밋 전에 차단한다.
        forged = store.create_job(
            kind="iceberg_import",
            dataset_id=bob_id,
            spec={"dataset_id": bob_id, "table_name": "alice_table"},
            owner_actor="bob",
        )
        assert run_once(store)
        assert store.get_job(forged["id"])["status"] == "Failed"
        assert len(iceberg_catalog(store).load_table("default.alice_table").snapshots()) == 2


def test_unowned_existing_catalog_table_is_not_silently_claimed(workspace):
    client, store = workspace
    from sos_platform.datasets import iceberg_catalog

    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    catalog.create_table("default.external_table", schema=pa.schema([("v", pa.int64())]))
    source = client.post(
        "/api/datasets/files",
        files={"file": ("x.csv", b"v\n1\n", "text/csv")},
    )
    assert source.status_code == 201
    denied = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source.json()["id"], "table_name": "external_table"},
    )
    assert denied.status_code == 404
    assert store.iceberg_write_claim("external_table") is None
    assert len(catalog.load_table("default.external_table").snapshots()) == 0


def test_recreated_table_with_same_token_cannot_reuse_write_authorization(workspace):
    """동일 이름·토큰으로 새로 만들어진 물리 테이블에는 기존 쓰기 권한이 없다."""
    client, store = workspace
    uploaded = client.post(
        "/api/datasets/files",
        files={"file": ("guard.csv", b"v\n1\n", "text/csv")},
    )
    assert uploaded.status_code == 201
    request = {"dataset_id": uploaded.json()["id"], "table_name": "identity_guard"}
    posted = client.post("/api/iceberg/import", json=request)
    assert posted.status_code == 202, posted.text
    assert run_once(store)
    assert store.get_job(posted.json()["id"])["status"] == "Succeeded"
    catalog = iceberg_catalog(store)
    original = catalog.load_table("default.identity_guard")
    claim = store.iceberg_write_claim("identity_guard")
    assert claim["state"] == "Active"
    assert claim["table_uuid"] == str(original.metadata.table_uuid)

    catalog.drop_table("default.identity_guard")
    replacement = catalog.create_table(
        "default.identity_guard",
        schema=pa.schema([("v", pa.int64())]),
        properties={"sos.write-token": claim["token"]},
    )
    assert str(replacement.metadata.table_uuid) != claim["table_uuid"]
    assert client.post("/api/iceberg/import", json=request).status_code == 404
    assert store.iceberg_write_claim("identity_guard")["table_uuid"] == claim["table_uuid"]
    assert replacement.current_snapshot() is None


def test_reservation_can_recover_after_table_creation_before_activation(workspace):
    """Catalog 생성과 DB 활성화 사이의 중단은 같은 예약 토큰으로 복구한다."""
    client, store = workspace
    uploaded = client.post(
        "/api/datasets/files",
        files={"file": ("staged.csv", b"v\n1\n", "text/csv")},
    )
    assert uploaded.status_code == 201
    claim = store.reserve_iceberg_table("pending_create", "local-developer")
    assert claim["state"] == "Reserved"
    assert claim["table_uuid"] is None
    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    created = catalog.create_table(
        "default.pending_create",
        schema=pa.schema([("v", pa.int64())]),
        properties={"sos.write-token": claim["token"]},
    )
    queued = client.post(
        "/api/iceberg/import",
        json={"dataset_id": uploaded.json()["id"], "table_name": "pending_create"},
    )
    assert queued.status_code == 202, queued.text
    completed = store.iceberg_write_claim("pending_create")
    assert completed["state"] == "Active"
    assert completed["table_uuid"] == str(created.metadata.table_uuid)
    assert run_once(store)
    assert store.get_job(queued.json()["id"])["status"] == "Succeeded"
