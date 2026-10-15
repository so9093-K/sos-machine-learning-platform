"""Error Catalog 코드의 중복·CLI 정합성·비밀정보 보호 정책."""

import ast
import re
from pathlib import Path

import pytest

from sos_platform.errors import CLI_CODES, DEFINITIONS, ERRORS, PlatformError
from sos_platform.observability import emit


def test_error_catalog_codes_are_unique_and_stable():
    names = [item.code for item in DEFINITIONS]
    assert len(names) == len(set(names))
    assert len(CLI_CODES) == len(set(CLI_CODES))
    for name, entry in ERRORS.items():
        assert re.fullmatch(r"SOS-[A-Z0-9]+(?:-[A-Z0-9]+)+", name)
        assert name == entry.code
        assert entry.message and entry.category and entry.title
        assert entry.retry_policy in (
            "never", "after_delay", "after_refresh", "manual_verification_required"
        )


def test_unknown_error_code_fails_closed():
    with pytest.raises(ValueError, match="Unregistered"):
        PlatformError("SOS-UNKNOWN-999")


def test_cli_startup_codes_are_cataloged():
    source = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "StartupError"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            assert call.args[0].value in ERRORS


def test_structured_logs_discard_unregistered_and_sensitive_values(caplog):
    emit(
        "worker.execution.failed",
        service="sos-worker",
        job_id="job-1",
        exception_type="RuntimeError",
        stage="publish",
        request_id="private secret password=foo",
    )
    # 별도의 Handler를 사용하므로 LogRecord의 내용에 비밀값이 등장하지 않는다.
    assert "private secret password=foo" not in caplog.text
    with pytest.raises(ValueError, match="Unregistered"):
        emit("worker.execution.failed", service="sos-worker", secret="private")
