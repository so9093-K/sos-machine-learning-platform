"""캐시의 할당량·사용 중 원본 보호와 재다운로드를 검증한다."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
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


def test_parallel_readers_share_the_same_cache_lease(workspace):
    """한 쿼리의 읽기 Lease가 동일 원본의 다른 읽기를 직렬화하지 않는다."""
    _client, store = workspace
    data = b"signal\\n1\\n2\\n".replace(b"\\n", b"\n")
    dataset = save_upload_file(store, "concurrent.csv", BytesIO(data))
    policy = CachePolicy(max_bytes=256 * 1024 * 1024)

    def read_again():
        with cached_dataset_file(store, dataset, policy) as path:
            return path.read_bytes()

    with cached_dataset_file(store, dataset, policy) as first:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # 첫 번째 Lease를 보유한 채 두 번째 읽기가 완료돼야 한다.
            assert pool.submit(read_again).result(timeout=5) == data
        assert first.read_bytes() == data


def test_unchanged_immutable_cache_avoids_rehash_and_detects_local_corruption(
    workspace, monkeypatch
):
    """반복 읽기는 불필요한 전체 해시를 피하되 파일 변조 후에는 S3 원본을 검증한다."""
    import sos_platform.cache as cache

    _client, store = workspace
    content = b"t,value\n1,2\n3,4\n"
    dataset = save_upload_file(store, "verified.csv", BytesIO(content))
    real_digest = cache._digest
    hashed = 0

    def counting_digest(path):
        nonlocal hashed
        hashed += 1
        return real_digest(path)

    monkeypatch.setattr(cache, "_digest", counting_digest)
    with cached_dataset_file(store, dataset) as local_path:
        assert local_path.read_bytes() == content
    with cached_dataset_file(store, dataset) as local_path:
        assert local_path.read_bytes() == content
    assert hashed == 0

    # 동일 크기의 외부 수정은 파일시스템 ctime 변경으로 감지된다.
    # 원본 S3 파일은 변경되지 않았으므로 다음 Lease에서 올바른 내용을 다시 받는다.
    corrupt = b"t,value\n9,9\n8,8\n"
    assert len(corrupt) == len(content)
    local_path.write_bytes(corrupt)
    with cached_dataset_file(store, dataset) as verified:
        assert verified.read_bytes() == content
    assert hashed >= 1


def test_abandoned_download_is_reclaimed_without_touching_other_cache_files(workspace):
    """다운로드 중 종료된 캐시 전용 임시 파일만 소유 잠금 아래 회수한다."""
    _client, store = workspace
    content = b"t,value\\n1,2\\n3,4\\n".replace(b"\\n", b"\n")
    dataset = save_upload_file(store, "recovered.csv", BytesIO(content))
    root = store.home / "cache"
    abandoned = root / f"{dataset['version']}.csv.pending-{'a' * 32}"
    unrelated = root / f"{dataset['version']}.csv.pending-user-file"
    abandoned.write_bytes(b"x" * 100)
    unrelated.write_bytes(b"do not touch")
    # cache/ 아래의 다른 이름으로 만든 링크나 외부 파일도 축출 대상이 아니다.
    external = store.home / "other-file.csv"
    external.write_bytes(b"outside" * 40)
    linked = root / f"{'b' * 64}.csv"
    linked.symlink_to(external)
    # 내용이 진짜와 같아도 이 캐시 파일명은 링크이므로 신뢰하지 않는다.
    expected = root / f"{dataset['version']}.csv"
    valid_external = store.home / "matching-file.csv"
    valid_external.write_bytes(content)
    expected.symlink_to(valid_external)

    with cached_dataset_file(store, dataset, CachePolicy(max_bytes=len(content) + 1)) as path:
        assert path.read_bytes() == content
        assert not path.is_symlink()
    assert not abandoned.exists()
    assert unrelated.read_bytes() == b"do not touch"
    assert linked.is_symlink()
    assert external.read_bytes() == b"outside" * 40
    assert valid_external.read_bytes() == content
