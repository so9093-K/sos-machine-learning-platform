"""객체 삭제·관측 정책의 순수 규칙 검증 (외부 저장소 불필요)."""

import pytest



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
