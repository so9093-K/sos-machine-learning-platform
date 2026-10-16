"""캐시의 할당량·사용 중 원본 보호와 재다운로드를 검증한다."""

import hashlib
from io import BytesIO

import pytest

from sos_platform.cache import CachePolicy, cached_dataset_file
from sos_platform.datasets import pinned_scan_dataset, save_upload_file, scan_dataset
from sos_platform.errors import PlatformError
from sos_platform.object_store import ObjectStore


def test_cache_quota_preserves_active_reader_and_source(workspace):
    _client, store = workspace
    first_data = b"t,value\n1,2\n3,4\n"
    second_data = b"t,value\n10,20\n30,40\n"
    first = save_upload_file(store, "first.csv", BytesIO(first_data))
    second = save_upload_file(store, "second.csv", BytesIO(second_data))
    assert first["version"] == hashlib.sha256(first_data).hexdigest()
    policy = CachePolicy(max_bytes=max(len(first_data), len(second_data)) + 1)
    with cached_dataset_file(store, first, policy) as path:
        assert path.read_bytes() == first_data
        with pytest.raises(PlatformError) as failure:
            with cached_dataset_file(store, second, policy):
                pytest.fail("Pinned cache file must not be evicted")
        assert failure.value.code == "SOS-CACHE-CAPACITY"
        assert path.is_file()
    with cached_dataset_file(store, second, policy) as path:
        assert path.read_bytes() == second_data
        assert not (store.home / "cache" / f"{first['version']}.csv").exists()
    # 축출은 읽기 가능한 S3 원본 객체를 삭제하지 않는다.
    storage = ObjectStore()
    assert storage.client.head_object(
        Bucket=storage.config.s3_bucket,
        Key=storage.key(first["location"]),
    )["ContentLength"] == len(first_data)


def test_file_scan_requires_a_pinned_read_lease(workspace):
    """캐시 상한과 Lease를 거치지 않고 파일 LazyFrame을 생성할 수 없다."""
    _client, store = workspace
    data = b"signal\\n1\\n2\\n".replace(b"\\n", b"\n")
    dataset = save_upload_file(store, "guarded.csv", BytesIO(data))
    with pytest.raises(RuntimeError, match="pinned_scan_dataset"):
        scan_dataset(store, dataset)
    with pinned_scan_dataset(store, dataset) as scan:
        assert scan.collect().height == 2
