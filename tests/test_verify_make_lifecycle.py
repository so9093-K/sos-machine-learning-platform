"""Make Lifecycle의 중단 시 안전한 정리 경로 검증."""

import importlib.util
import signal
import sys
from pathlib import Path

scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
source = scripts / "verify_make_lifecycle.py"
spec = importlib.util.spec_from_file_location("sos_test_make_lifecycle", source)
assert spec is not None and spec.loader is not None
lifecycle = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lifecycle
spec.loader.exec_module(lifecycle)


def test_sigterm_path_reports_failure_and_cleans_owned_environment(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    signals = []
    calls = []
    monkeypatch.setattr(lifecycle.signal, "signal", lambda sig, fn: signals.append((sig, fn)))
    monkeypatch.setattr(lifecycle.secrets, "token_hex", lambda _size: "123456abcdef")
    monkeypatch.setattr(lifecycle, "free_ports", lambda: [10001, 10002, 10003, 10004])
    monkeypatch.setattr(lifecycle, "isolated_environment", lambda _src, _id, _ports: {
        "SOS_DEV_INSTANCE": instance
    })
    monkeypatch.setattr(lifecycle, "create_owner", lambda _id, _ports: tmp_path)

    def interrupted(_env):
        signals[0][1](signal.SIGTERM, None)

    monkeypatch.setattr(lifecycle, "verify", interrupted)
    monkeypatch.setattr(lifecycle, "clean_owned", lambda i, path, env: (
        calls.append((i, path, env)) or 0
    ))
    assert lifecycle.main() == 1
    assert signals[0][0] == signal.SIGTERM
    assert calls == [(instance, tmp_path, {"SOS_DEV_INSTANCE": instance})]


def test_cleanup_failure_prevents_success(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(lifecycle.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(lifecycle.secrets, "token_hex", lambda _size: "123456abcdef")
    monkeypatch.setattr(lifecycle, "free_ports", lambda: [10001, 10002, 10003, 10004])
    monkeypatch.setattr(lifecycle, "isolated_environment", lambda _src, _id, _ports: {
        "SOS_DEV_INSTANCE": instance
    })
    monkeypatch.setattr(lifecycle, "create_owner", lambda _id, _ports: tmp_path)
    monkeypatch.setattr(lifecycle, "verify", lambda _env: None)
    monkeypatch.setattr(lifecycle, "clean_owned", lambda _i, _path, _env: 1)
    assert lifecycle.main() == 1
