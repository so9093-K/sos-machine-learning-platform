"""Storage Health·객체 관측 통합 계약을 검증한다."""

from uuid import uuid4

import pytest
from sqlalchemy import event, text

from sos_platform.object_store import ObjectStore
from sos_platform.worker import run_once

from .support import upload


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
