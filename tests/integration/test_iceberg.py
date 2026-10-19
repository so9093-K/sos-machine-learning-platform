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
    assert refused.json()["code"] == "SOS-ICEBERG-CANCEL-409"
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

    before = len(store.dataset_page(limit=101))

    def fail_tag(_table):
        raise RuntimeError("Tag commit unavailable")

    monkeypatch.setattr(Table, "manage_snapshots", fail_tag)
    with pytest.raises(RuntimeError, match="Tag commit unavailable"):
        connect_iceberg(store, "tag_failure")
    assert len(store.dataset_page(limit=101)) == before
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
    published = import_to_iceberg(
        store, source, "interleaved_import", str(uuid4()), owner_actor="local-developer"
    )
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


def test_two_independent_writers_race_on_existing_iceberg_table(workspace, monkeypatch):
    """서로 다른 Catalog 세션이 같은 기준 Snapshot에서 동시에 Append를 시작한다."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from pyiceberg.table import Table

    from sos_platform.datasets import iceberg_catalog, import_to_iceberg
    from sos_platform.retention import inspect_tag, tag_name
    from sos_platform.store import Store

    client, store = workspace
    source = upload(client)
    table_name = "racing_writers"
    baseline = import_to_iceberg(
        store, source, table_name, str(uuid4()), owner_actor="local-developer"
    )
    assert baseline["version"]

    attempts = [str(uuid4()), str(uuid4())]
    rendezvous = Barrier(2)
    original_append = Table.append

    def synchronized_append(table, data, **kwargs):
        if kwargs.get("snapshot_properties", {}).get("sos.attempt-id") in attempts:
            rendezvous.wait(timeout=20)
        return original_append(table, data, **kwargs)

    monkeypatch.setattr(Table, "append", synchronized_append)

    def writer(attempt_id):
        isolated = Store(store.home, database_url=store.database_url)
        try:
            return import_to_iceberg(
                isolated, source, table_name, attempt_id, owner_actor="local-developer"
            )
        finally:
            isolated.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(writer, attempt_id) for attempt_id in attempts]
        outcomes = []
        for future in futures:
            try:
                outcomes.append((future.result(timeout=45), None))
            except Exception as exc:
                outcomes.append((None, exc))

    # 하나 이상이 커밋되어야 하며 충돌 시 자동으로 중복 Append하지 않는다.
    assert any(result is not None for result, _error in outcomes), outcomes
    table = iceberg_catalog(store).load_table(f"default.{table_name}")
    snapshots = table.snapshots()
    assert 2 <= len(snapshots) <= 3
    assert table.scan().to_arrow().num_rows == 3 * len(snapshots)
    for attempt_id, (result, error) in zip(attempts, outcomes, strict=True):
        matching = [
            snapshot
            for snapshot in snapshots
            if snapshot.summary and snapshot.summary.get("sos.attempt-id") == attempt_id
        ]
        assert len(matching) <= 1, (attempt_id, error)
        if result is not None:
            assert len(matching) == 1
            assert result["version"] == str(matching[0].snapshot_id)
            assert (
                inspect_tag(
                    table,
                    name=tag_name(result["retention_id"]),
                    snapshot_id=result["version"],
                )
                == "retained"
            )
            assert store.snapshot_retention(result["retention_id"])["state"] == "Pending"
    assert not [row for row in store.dataset_page(limit=101) if row["kind"] == "iceberg"]


@pytest.mark.pr
def test_iceberg_commit_response_loss_preserves_snapshot_without_automatic_retry(
    workspace, monkeypatch, tmp_path
):
    """외부 Snapshot 커밋 후 ACK 유실은 미실행이 아닌 불확실성으로 보존한다."""
    import json

    from pyiceberg.table import Table

    import sos_platform.worker as worker
    from sos_platform.datasets import iceberg_catalog

    client, store = workspace
    source = upload(client)
    request = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source["id"], "table_name": "lost_commit_response"},
    )
    assert request.status_code == 202
    claimed = store.claim()
    assert claimed["id"] == request.json()["id"]
    attempt_id = claimed["attempts"][-1]["id"]
    completed = tmp_path / "lost-ack.completed.json"
    pending = tmp_path / "lost-ack.pending.parquet"
    original_append = Table.append
    commit_count = 0
    secret = "token=never-expose-this"

    def commit_then_lose_response(table, data, **kwargs):
        nonlocal commit_count
        original_append(table, data, **kwargs)
        commit_count += 1
        raise TimeoutError(secret)

    with monkeypatch.context() as patch:
        patch.setattr(worker, "_guard_child_parent", lambda _pid: None)
        patch.setattr(Table, "append", commit_then_lose_response)
        worker._perform(store.home, store.database_url, claimed, completed, pending, 0)

    outcome = json.loads(completed.read_text(encoding="utf-8"))
    assert commit_count == 1
    assert outcome["error_code"] == "SOS-ICEBERG-UNCERTAIN"
    assert outcome["failure_stage"] == "iceberg_commit"
    assert outcome["failure_category"] == "timeout"
    assert secret not in completed.read_text(encoding="utf-8")

    # Worker의 외부 커밋 결과는 DB에 성공으로 기록되지 않았다.
    assert store.get_job(claimed["id"])["status"] == "Running"
    assert not [d for d in store.dataset_page(limit=101) if d["kind"] == "iceberg"]
    table = iceberg_catalog(store).load_table("default.lost_commit_response")
    snapshots = [
        snapshot
        for snapshot in table.snapshots()
        if snapshot.summary and snapshot.summary.get("sos.attempt-id") == attempt_id
    ]
    assert len(snapshots) == 1
    assert table.scan().to_arrow().num_rows == 3
    assert not any(name.startswith("sos_dataset_") for name in table.metadata.refs)

    # 부모 SIGKILL과 동일하게 완료 메시지를 잃으면 새 Worker는 다시 Append하지 않는다.
    recovered = store.recover()
    assert (claimed["id"], attempt_id) in recovered
    job = store.get_job(claimed["id"])
    assert job["status"] == "Failed"
    assert job["error_code"] == "SOS-ICEBERG-UNCERTAIN"
    assert job["attempts"][-1]["status"] == "Interrupted"
    assert attempt_id in job["error"]
    assert run_once(store) is False
    assert len(iceberg_catalog(store).load_table("default.lost_commit_response").snapshots()) == 1
    assert not [d for d in store.dataset_page(limit=101) if d["kind"] == "iceberg"]


def test_tag_commit_ack_loss_preserves_uncertain_retention_and_catalog_tag(workspace, monkeypatch):
    """보호 Tag 커밋 응답 유실에도 미게시 의도와 실제 Tag를 함께 보존한다."""
    from pyiceberg.table import Table

    from sos_platform.datasets import connect_iceberg, iceberg_catalog
    from sos_platform.retention import inspect_tag

    client, store = workspace
    source = upload(client)
    job = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source["id"], "table_name": "lost_tag_response"},
    )
    assert job.status_code == 202
    assert run_once(store)
    table = iceberg_catalog(store).load_table("default.lost_tag_response")
    snapshot_id = str(table.current_snapshot().snapshot_id)
    before = len(store.dataset_page(limit=101))
    original_manage = Table.manage_snapshots

    class LostTagAcknowledgement:
        def __init__(self, delegate):
            self.delegate = delegate

        def create_tag(self, snapshot, name):
            self.delegate = self.delegate.create_tag(snapshot, name)
            return self

        def commit(self):
            self.delegate.commit()
            raise TimeoutError("tag acknowledgement was lost")

    with monkeypatch.context() as patch:
        patch.setattr(
            Table,
            "manage_snapshots",
            lambda table: LostTagAcknowledgement(original_manage(table)),
        )
        with pytest.raises(TimeoutError, match="acknowledgement was lost"):
            connect_iceberg(store, "lost_tag_response", snapshot_id)

    assert len(store.dataset_page(limit=101)) == before
    with store.connection() as db:
        records = (
            db.execute(
                text(
                    "SELECT dataset_id,tag_name,state FROM snapshot_retention "
                    "WHERE state='Uncertain'"
                )
            )
            .mappings()
            .all()
        )
    assert len(records) == 1
    retained = records[0]
    assert store.get_dataset(retained["dataset_id"]) is None
    table = iceberg_catalog(store).load_table("default.lost_tag_response")
    assert inspect_tag(table, name=retained["tag_name"], snapshot_id=snapshot_id) == "retained"
    checked = client.get("/api/storage/iceberg-references")
    assert checked.status_code == 200
    unresolved = next(row for row in checked.json() if row["dataset_id"] == retained["dataset_id"])
    assert unresolved["status"] == "unknown"


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


def test_partitioned_large_import_rejects_before_staging(workspace, monkeypatch):
    """기존 파티션 테이블의 호환 한도는 대용량 임시 파일 작성 전에 검사한다."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    import polars as pl

    import sos_platform.datasets as module

    _client, store = workspace

    claim = store.reserve_iceberg_table("partitioned_input", "local-developer")

    class ExistingTable:
        metadata = SimpleNamespace(
            properties={"sos.write-token": claim["token"]},
            table_uuid=uuid4(),
        )

        def spec(self):
            return SimpleNamespace(fields=["part"])

    class ExistingCatalog:
        def create_namespace_if_not_exists(self, _name):
            pass

        def load_table(self, _name):
            return ExistingTable()

    @contextmanager
    def fake_source(_store, _dataset):
        yield pl.DataFrame({"value": range(50_001)}).lazy()

    monkeypatch.setattr(module, "iceberg_catalog", lambda _store: ExistingCatalog())
    monkeypatch.setattr(module, "pinned_scan_dataset", fake_source)
    with pytest.raises(ValueError, match="50,000"):
        module.import_to_iceberg(
            store,
            {"kind": "file"},
            "partitioned_input",
            str(uuid4()),
            owner_actor="local-developer",
        )
    assert not (store.home / "cache" / "iceberg-staging").exists()


@pytest.mark.parametrize("failure_type", [ValueError, RuntimeError])
def test_snapshot_retention_failure_never_exposes_catalog_error(
    workspace, monkeypatch, failure_type
):
    """외부 Tag 커밋이 불확실하면 재실행을 권하지 않고 내부 오류 원문을 숨긴다."""
    import sos_platform.api as platform_api

    client, store = workspace
    item = store.register_dataset(
        name="unverified_snapshot",
        kind="iceberg",
        location="default.private_table",
        version="1234",
        owner_actor="local-developer",
    )

    def uncertain_tag(*_args, **_kwargs):
        raise failure_type("private catalog credential and internal path")

    monkeypatch.setattr(platform_api, "ensure_tag", uncertain_tag)
    response = client.post(f"/api/storage/iceberg-references/{item['id']}/retain")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SOS-ICEBERG-RETAIN"
    assert body["retry_policy"] == "manual_verification_required"
    assert "private catalog" not in response.text
    assert body["request_id"] == response.headers["X-Request-ID"]
