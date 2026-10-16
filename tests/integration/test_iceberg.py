"""Iceberg Snapshot·Tag 통합 계약을 검증한다."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from sos_platform.worker import run_once

from .support import upload


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
    assert recovered["attempts"][0]["id"] in recovered["error"]
    assert any(
        item["action"] == "job.recover"
        and item["resource_id"] == created["id"]
        and item["outcome"] == "interrupted"
        and recovered["attempts"][0]["id"] in item["reason"]
        for item in store.audit_events(limit=100)
    )
    assert run_once(store) is False


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
    priority = client.get("/api/storage/iceberg-references?limit=1")
    assert priority.status_code == 200
    assert priority.json()[0]["dataset_id"] == rows[0]["dataset_id"]


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


def test_tag_survives_database_publish_failure(workspace, monkeypatch):
    """Catalog Tag 커밋 후 DB 게시 실패는 미게시 의도로 남아 운영자가 확인할 수 있다."""
    client, store = workspace
    source = upload(client)
    request = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source["id"], "table_name": "publish_failure"},
    )
    assert request.status_code == 202
    assert run_once(store)

    from sos_platform.datasets import connect_iceberg, iceberg_catalog
    from sos_platform.retention import inspect_tag, tag_name

    def refuse_publish(**_kwargs):
        raise RuntimeError("Simulated database publication failure")

    monkeypatch.setattr(store, "register_dataset", refuse_publish)
    with pytest.raises(RuntimeError, match="Simulated database publication failure"):
        connect_iceberg(store, "publish_failure")

    with store.connection() as db:
        orphan = (
            db.execute(
                text(
                    "SELECT r.dataset_id,r.snapshot_id FROM snapshot_retention r "
                    "LEFT JOIN datasets d ON r.dataset_id=d.id "
                    "WHERE d.id IS NULL AND r.state='Pending'"
                )
            )
            .mappings()
            .one()
        )
    table = iceberg_catalog(store).load_table("default.publish_failure")
    assert (
        inspect_tag(table, name=tag_name(orphan["dataset_id"]), snapshot_id=orphan["snapshot_id"])
        == "retained"
    )
    inspection = client.get("/api/storage/iceberg-references?limit=1")
    assert inspection.status_code == 200
    assert inspection.json()[0]["dataset_id"] == orphan["dataset_id"]
    assert inspection.json()[0]["status"] == "unknown"


def test_large_unpartitioned_iceberg_import_uses_one_snapshot(workspace):
    """과거 50,000행 제한을 넘는 입력도 한번의 논리적 커밋으로 적재한다."""
    from io import BytesIO

    import pyarrow as pa
    import pyarrow.parquet as pq

    from sos_platform.datasets import iceberg_catalog

    client, store = workspace
    buffer = BytesIO()
    pq.write_table(
        pa.table(
            {
                "value": pa.array(range(65_000), type=pa.int64()),
                "label": pa.array(["unit"] * 65_000),
            }
        ),
        buffer,
    )
    uploaded = client.post(
        "/api/datasets/files",
        files={"file": ("large-import.parquet", buffer.getvalue(), "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    job = client.post(
        "/api/iceberg/import",
        json={
            "dataset_id": uploaded.json()["id"],
            "table_name": "large_import",
        },
    )
    assert job.status_code == 202
    assert run_once(store)
    finished = client.get(f"/api/jobs/{job.json()['id']}").json()
    assert finished["status"] == "Succeeded", finished
    table = iceberg_catalog(store).load_table("default.large_import")
    assert len(table.snapshots()) == 1
    assert table.scan().to_arrow().num_rows == 65_000
    assert table.current_snapshot().summary["sos.attempt-id"] == finished["attempts"][0]["id"]


def test_iceberg_staging_cleanup_is_scoped_to_its_attempt(workspace):
    """강제 종료 후에는 해당 Attempt의 임시 파일만 정리할 수 있어야 한다."""
    from sos_platform.datasets import cleanup_iceberg_staging

    _client, store = workspace
    first, second = str(uuid4()), str(uuid4())
    root = store.home / "cache" / "iceberg-staging"
    own = root / f"attempt-{first}-test1"
    other = root / f"attempt-{second}-test2"
    for path in (own, other):
        path.mkdir(parents=True, exist_ok=True)
        (path / "input.parquet").write_bytes(b"partially-written")
    unexpected = root / "user-original.parquet"
    unexpected.write_bytes(b"keep-me")
    cleanup_iceberg_staging(store, first)
    assert not own.exists()
    assert other.exists()
    assert unexpected.read_bytes() == b"keep-me"
    cleanup_iceberg_staging(store)
    assert not other.exists()
    assert unexpected.read_bytes() == b"keep-me"
    unexpected.unlink()


def test_staging_size_tracks_only_the_target_attempt(workspace):
    """임시 디스크 관측은 타 Attempt 및 무관한 캐시 파일을 포함하지 않는다."""
    from sos_platform.datasets import cleanup_iceberg_staging, iceberg_staging_bytes

    _client, store = workspace
    first, other = str(uuid4()), str(uuid4())
    root = store.home / "cache" / "iceberg-staging"
    path_a = root / f"attempt-{first}-aaaa"
    path_b = root / f"attempt-{other}-bbbb"
    path_a.mkdir(parents=True, exist_ok=True)
    path_b.mkdir(parents=True, exist_ok=True)
    (path_a / "input.parquet").write_bytes(b"a" * 19)
    (path_b / "input.parquet").write_bytes(b"b" * 29)
    assert iceberg_staging_bytes(store, first) == 19
    assert iceberg_staging_bytes(store, other) == 29
    cleanup_iceberg_staging(store)
    assert iceberg_staging_bytes(store, first) == 0
