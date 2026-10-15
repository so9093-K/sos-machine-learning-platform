"""인프라 없는 파일 수신 자원 한도와 임시 파일의 수명주기를 검증한다."""

import asyncio
import hashlib
from io import BytesIO

import pytest
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from sos_platform.errors import PlatformError
from sos_platform.ingestion import CHUNK_SIZE, UploadPolicy, staged_upload


class RepeatedStream:
    """원본 크기와 무관하게 최대 1 MiB 청크만 생성한다."""

    def __init__(self, size: int):
        self.remaining = size
        self.max_read = 0

    def read(self, size: int) -> bytes:
        self.max_read = max(self.max_read, size)
        count = min(size, self.remaining)
        self.remaining -= count
        return b"x" * count


def test_staging_streams_76_mib_and_cleans_up(tmp_path):
    source = RepeatedStream(76 * 1024 * 1024)
    with staged_upload(source, "large.parquet", tmp_path, max_bytes=80 * 1024 * 1024) as item:
        assert item.size_bytes == 76 * 1024 * 1024
        assert item.path.stat().st_size == item.size_bytes
        with item.path.open("rb") as handle:
            assert item.sha256 == hashlib.file_digest(handle, "sha256").hexdigest()
        staged_path = item.path
    assert source.max_read == CHUNK_SIZE
    assert not staged_path.exists()


def test_staging_rejects_oversize_and_removes_temporary(tmp_path):
    with pytest.raises(PlatformError) as exc:
        with staged_upload(BytesIO(b"12345"), "sample.csv", tmp_path, max_bytes=4):
            pytest.fail("oversized content must not be published")
    assert exc.value.code == "SOS-UPLOAD-TOO-LARGE"
    assert list(tmp_path.iterdir()) == []


def test_staging_rejects_empty_and_unsupported_format(tmp_path):
    with pytest.raises(PlatformError) as empty:
        with staged_upload(BytesIO(b""), "empty.csv", tmp_path, max_bytes=100):
            pytest.fail("empty file must not be accepted")
    assert empty.value.code == "SOS-UPLOAD-INVALID"
    with pytest.raises(PlatformError) as unsupported:
        with staged_upload(BytesIO(b"content"), "script.py", tmp_path, max_bytes=100):
            pytest.fail("unsupported format must not be accepted")
    assert unsupported.value.code == "SOS-UPLOAD-FORMAT"
    assert list(tmp_path.iterdir()) == []


def test_upload_policy_is_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("SOS_MAX_UPLOAD_MIB", "80")
    assert UploadPolicy.from_env().max_bytes == 80 * 1024 * 1024
    for raw in ("0", "2049", "invalid", "1.5", "-1"):
        monkeypatch.setenv("SOS_MAX_UPLOAD_MIB", raw)
        with pytest.raises(ValueError, match="SOS_MAX_UPLOAD_MIB"):
            UploadPolicy.from_env()


def test_staging_accepts_exact_byte_limit(tmp_path):
    with staged_upload(BytesIO(b"1234"), "limit.csv", tmp_path, max_bytes=4) as item:
        assert item.size_bytes == 4
    assert list(tmp_path.iterdir()) == []


def test_failed_source_read_cleans_staging_file(tmp_path):
    class FailingStream:
        calls = 0

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"first"
            raise OSError("read failed")

    with pytest.raises(OSError, match="read failed"):
        with staged_upload(FailingStream(), "broken.csv", tmp_path, max_bytes=100):
            pytest.fail("a failed stream must not be published")
    assert list(tmp_path.iterdir()) == []


def test_body_limit_counts_chunked_asgi_bytes_without_content_length():
    """헤더 크기 추측이 아닌 실제 ASGI body 청크의 합으로 요청을 제한한다."""

    async def scenario():
        messages = [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
        responses = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            responses.append(message)

        async def consume_body(_scope, receive_body, send_response):
            while (await receive_body()).get("more_body", False):
                pass
            await send_response({"type": "http.response.start", "status": 200, "headers": []})
            await send_response({"type": "http.response.body", "body": b""})

        middleware = RequestBodyLimitMiddleware(consume_body, max_body_size=5)
        await middleware(
            {"type": "http", "method": "POST", "path": "/api/datasets/files", "headers": []},
            receive,
            send,
        )
        return responses

    output = asyncio.run(scenario())
    assert output[0]["type"] == "http.response.start"
    assert output[0]["status"] == 413
