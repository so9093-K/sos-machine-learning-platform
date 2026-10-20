"""기존 Iceberg 테이블의 명시적 채택은 읽기와 쓰기를 분리해 복구한다."""

import os

import pyarrow as pa
import pytest
from sqlalchemy import text

from sos_platform.datasets import iceberg_catalog
from sos_platform.iceberg_ownership import adopt_legacy_table, inspect_legacy_table
from sos_platform.worker import run_once


def legacy_table(store, name="legacy_sensor"):
    catalog = iceberg_catalog(store)
    catalog.create_namespace_if_not_exists("default")
    table = catalog.create_table(f"default.{name}", schema=pa.schema([("v", pa.int64())]))
    table.append(pa.table({"v": [1, 2, 3]}))
    return catalog.load_table(f"default.{name}")


def evidence(store, table, owner="local-developer", name="legacy_sensor"):
    return store.register_dataset(
        name=name,
        kind="iceberg",
        location=f"default.{name}",
        version=str(table.current_snapshot().snapshot_id),
        owner_actor=owner,
    )


def adopt(store, table, source, *, name="legacy_sensor"):
    return adopt_legacy_table(
        store,
        table_name=name,
        owner_actor=source["owner_actor"],
        dataset_id=source["id"],
        expected_table_uuid=str(table.metadata.table_uuid),
        expected_snapshot_id=str(table.current_snapshot().snapshot_id),
    )


def test_legacy_inspect_is_read_only_then_explicit_adoption_restores_append(workspace):
    client, store = workspace
    table = legacy_table(store)
    source = evidence(store, table)
    report = inspect_legacy_table(store, "legacy_sensor")
    assert report["state"] == "candidate"
    assert report["owner_candidate"] == "local-developer"
    assert report["dataset_references"] == 1
    assert store.iceberg_write_claim("legacy_sensor") is None
    assert (
        "sos.write-token"
        not in iceberg_catalog(store).load_table("default.legacy_sensor").metadata.properties
    )
    uploaded = client.post(
        "/api/datasets/files",
        files={"file": ("extra.csv", b"v\n4\n", "text/csv")},
    )
    assert uploaded.status_code == 201
    denied = client.post(
        "/api/iceberg/import",
        json={"dataset_id": uploaded.json()["id"], "table_name": "legacy_sensor"},
    )
    assert denied.status_code == 404
    assert adopt(store, table, source)["state"] == "active"
    claimed = store.iceberg_write_claim("legacy_sensor")
    assert claimed["owner_actor"] == "local-developer"
    assert claimed["state"] == "Active"
    assert claimed["table_uuid"] == str(table.metadata.table_uuid)
    assert claimed["adoption_dataset_id"] == source["id"]
    after = iceberg_catalog(store).load_table("default.legacy_sensor")
    assert after.metadata.properties["sos.write-token"] == claimed["token"]
    assert len(after.snapshots()) == 1
    assert client.get(f"/api/datasets/{source['id']}/preview").status_code == 200
    queued = client.post(
        "/api/iceberg/import",
        json={"dataset_id": uploaded.json()["id"], "table_name": "legacy_sensor"},
    )
    assert queued.status_code == 202, queued.text
    assert run_once(store)
    assert store.get_job(queued.json()["id"])["status"] == "Succeeded"
    assert len(iceberg_catalog(store).load_table("default.legacy_sensor").snapshots()) == 2
    with store.connection() as db:
        events = list(
            db.execute(
                text(
                    "SELECT action,actor_id FROM audit_events WHERE resource_type='iceberg_table'"
                    " AND resource_id='default.legacy_sensor' ORDER BY at"
                )
            ).all()
        )
    assert {item[0] for item in events} == {
        "iceberg.table.adoption_started",
        "iceberg.table.adoption_completed",
    }
    assert all(item[1] == f"local-uid:{os.geteuid()}" for item in events)


def test_adoption_rejects_ambiguous_missing_and_stale_identity(workspace):
    _client, store = workspace
    table = legacy_table(store)
    source = evidence(store, table)
    wrong_uuid = str(__import__("uuid").uuid4())
    with pytest.raises(PermissionError, match="identity"):
        adopt_legacy_table(
            store,
            table_name="legacy_sensor",
            owner_actor="local-developer",
            dataset_id=source["id"],
            expected_table_uuid=wrong_uuid,
            expected_snapshot_id=str(table.current_snapshot().snapshot_id),
        )
    with pytest.raises(PermissionError, match="identity"):
        adopt_legacy_table(
            store,
            table_name="legacy_sensor",
            owner_actor="local-developer",
            dataset_id=source["id"],
            expected_table_uuid=str(table.metadata.table_uuid),
            expected_snapshot_id="999999999",
        )
    assert store.iceberg_write_claim("legacy_sensor") is None
    evidence(store, table, owner="bob")
    assert inspect_legacy_table(store, "legacy_sensor")["state"] == "ambiguous_owner"
    with pytest.raises(PermissionError):
        adopt(store, table, source)
    assert store.iceberg_write_claim("legacy_sensor") is None

    other = legacy_table(store, "legacy_unlinked")
    assert inspect_legacy_table(store, "legacy_unlinked")["state"] == "insufficient_evidence"
    assert other.metadata.properties.get("sos.write-token") is None


def test_adoption_commit_ack_or_db_failure_is_resumable_and_write_stays_closed(
    workspace, monkeypatch
):
    client, store = workspace
    table = legacy_table(store)
    source = evidence(store, table)
    original = store.complete_legacy_iceberg_adoption

    def lost_database_completion(*_args, **_kwargs):
        raise RuntimeError("simulated DB completion loss")

    with monkeypatch.context() as patch:
        patch.setattr(store, "complete_legacy_iceberg_adoption", lost_database_completion)
        with pytest.raises(RuntimeError, match="completion loss"):
            adopt(store, table, source)
    assert inspect_legacy_table(store, "legacy_sensor")["state"] == "resume_required"
    assert store.iceberg_write_claim("legacy_sensor")["state"] == "Adopting"
    uploaded = client.post(
        "/api/datasets/files",
        files={"file": ("later.csv", b"v\n5\n", "text/csv")},
    )
    assert uploaded.status_code == 201
    denied = client.post(
        "/api/iceberg/import",
        json={"dataset_id": uploaded.json()["id"], "table_name": "legacy_sensor"},
    )
    assert denied.status_code == 404
    assert store.complete_legacy_iceberg_adoption == original
    assert adopt(store, table, source)["state"] == "active"
    assert len(iceberg_catalog(store).load_table("default.legacy_sensor").snapshots()) == 1


def test_adoption_cannot_override_existing_catalog_owner_token(workspace):
    _client, store = workspace
    table = legacy_table(store)
    source = evidence(store, table)
    table.transaction().set_properties(
        {"sos.write-token": "owned-by-someone-else"}
    ).commit_transaction()
    assert inspect_legacy_table(store, "legacy_sensor")["state"] == "conflict"
    with pytest.raises(PermissionError):
        adopt(store, table, source)
    assert store.iceberg_write_claim("legacy_sensor") is None


def test_pending_adoption_rejects_different_dataset_reference(workspace, monkeypatch):
    """같은 Actor의 다른 Dataset도 중단된 채택 근거를 바꾸지 못한다."""
    _client, store = workspace
    table = legacy_table(store)
    source = evidence(store, table)
    alternate = evidence(store, table)

    def lost_completion(*_args, **_kwargs):
        raise RuntimeError("simulated uncertain completion")

    with monkeypatch.context() as patch:
        patch.setattr(store, "complete_legacy_iceberg_adoption", lost_completion)
        with pytest.raises(RuntimeError, match="uncertain"):
            adopt(store, table, source)
    with pytest.raises(PermissionError, match="evidence"):
        adopt(store, table, alternate)
    assert store.iceberg_write_claim("legacy_sensor")["adoption_dataset_id"] == source["id"]
    assert adopt(store, table, source)["state"] == "active"
