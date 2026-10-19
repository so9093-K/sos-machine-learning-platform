"""객체 삭제·관측 정책의 순수 규칙 검증 (외부 저장소 불필요)."""

from types import SimpleNamespace
from uuid import uuid4

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


def test_iceberg_staging_cleanup_is_scoped_to_its_attempt(tmp_path):
    """강제 종료 후에는 해당 Attempt의 임시 파일만 정리할 수 있어야 한다."""
    from sos_platform.datasets import cleanup_iceberg_staging

    store = SimpleNamespace(home=tmp_path)
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


def test_staging_size_tracks_only_the_target_attempt(tmp_path):
    """임시 디스크 관측은 타 Attempt 및 무관한 캐시 파일을 포함하지 않는다."""
    from sos_platform.datasets import cleanup_iceberg_staging, iceberg_staging_bytes

    store = SimpleNamespace(home=tmp_path)
    first, other = str(uuid4()), str(uuid4())
    root = store.home / "cache" / "iceberg-staging"
    path_a = root / f"attempt-{first}-aaaa"
    path_b = root / f"attempt-{other}-bbbb"
    path_a.mkdir(parents=True, exist_ok=True)
    path_b.mkdir(parents=True, exist_ok=True)
    (path_a / "input.parquet").write_bytes(b"a" * 19)
    (path_b / "input.parquet").write_bytes(b"b" * 29)
    (path_a / "spill.tmp").write_bytes(b"x" * 11)
    nested = path_a / "scratch"
    nested.mkdir()
    (nested / "disk.bin").write_bytes(b"y" * 13)
    outside = root / "user-file.bin"
    outside.write_bytes(b"z" * 101)
    (path_a / "outside-link").symlink_to(outside)
    (path_a / "other-attempt-link").symlink_to(path_b, target_is_directory=True)
    assert iceberg_staging_bytes(store, first) == 43
    assert iceberg_staging_bytes(store, other) == 29
    cleanup_iceberg_staging(store)
    assert iceberg_staging_bytes(store, first) == 0
    assert outside.read_bytes() == b"z" * 101
    outside.unlink()
