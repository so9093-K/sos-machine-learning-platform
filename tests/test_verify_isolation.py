"""격리 E2E 자원의 소유권과 정리 실패 계약."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).resolve().parents[1] / "scripts" / "run_isolated_e2e.py"
spec = importlib.util.spec_from_file_location("sos_test_isolated_e2e", source)
assert spec is not None and spec.loader is not None
isolated = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = isolated
spec.loader.exec_module(isolated)


def test_cleanup_rejects_missing_or_foreign_owner(tmp_path):
    instance = "sos-e2e-123456abcdef"
    env = {"SOS_DEV_INSTANCE": instance}
    assert isolated.clean_owned(instance, tmp_path / instance, env) != 0
    runtime = tmp_path / instance
    runtime.mkdir()
    (runtime / isolated.OWNER_FILE).write_text(
        json.dumps({"purpose": "sos-test", "instance": "sos-platform", "ports": [1, 2, 3, 4]})
    )
    assert isolated.clean_owned(instance, runtime, env) != 0


def test_cleanup_failure_is_visible_and_owned_marker_preserved(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = isolated.create_owner(instance, [10001, 10002, 10003, 10004])
    assert isinstance(runtime, Path)
    calls = []

    def fail(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(isolated.subprocess, "run", fail)
    assert isolated.clean_owned(instance, runtime, {"SOS_DEV_INSTANCE": instance}) != 0
    assert len(calls) == 2
    assert (runtime / isolated.OWNER_FILE).is_file()


def test_token_mode_generates_only_ephemeral_roles():
    env = {"SOS_AUTH_MODE": "development"}
    isolated.configure_tokens(env)
    identities = json.loads(env["SOS_AUTH_TOKEN_HASHES"])
    assert {record["roles"][0] for record in identities.values()} == {
        "viewer",
        "researcher",
        "operator",
    }
    assert "ci-viewer" in {record["actor_id"] for record in identities.values()}
    assert all(len(key) == 64 for key in identities)
