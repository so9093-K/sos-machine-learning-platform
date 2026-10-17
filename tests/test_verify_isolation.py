"""격리 E2E 자원의 소유권과 정리 실패 계약."""

import importlib.util
import json
import subprocess
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
    # 서비스 종료 실패 시 볼륨을 삭제하지 않고 복구 기록을 남긴다.
    assert len(calls) == 1
    assert (runtime / isolated.OWNER_FILE).is_file()



def test_cleanup_success_only_removes_owned_project(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = isolated.create_owner(instance, [10001, 10002, 10003, 10004])
    foreign = tmp_path / "other-project"
    foreign.mkdir()
    (foreign / "important.txt").write_text("keep", encoding="utf-8")
    calls = []

    def succeed(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(isolated.subprocess, "run", succeed)
    assert isolated.clean_owned(instance, runtime, {"SOS_DEV_INSTANCE": instance}) == 0
    assert not runtime.exists()
    assert (foreign / "important.txt").read_text(encoding="utf-8") == "keep"
    assert calls[0] == ["python3", "scripts/dev.py", "down"]
    assert calls[1] == [
        "docker", "compose", "-f", "compose.yaml", "-p", instance, "down", "--volumes"
    ]


def test_cleanup_compose_failure_preserves_owned_marker(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = isolated.create_owner(instance, [10001, 10002, 10003, 10004])
    calls = []

    def process(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1 if command[0] == "docker" else 0)

    monkeypatch.setattr(isolated.subprocess, "run", process)
    assert isolated.clean_owned(instance, runtime, {"SOS_DEV_INSTANCE": instance}) != 0
    assert len(calls) == 2
    assert (runtime / isolated.OWNER_FILE).exists()


def test_cleanup_timeout_never_removes_owned_marker(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = isolated.create_owner(instance, [10001, 10002, 10003, 10004])

    def timeout(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 240)

    monkeypatch.setattr(isolated.subprocess, "run", timeout)
    assert isolated.clean_owned(instance, runtime, {"SOS_DEV_INSTANCE": instance}) != 0
    assert (runtime / isolated.OWNER_FILE).is_file()


def test_recovery_refuses_invalid_or_foreign_owner_ports(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = tmp_path / instance
    runtime.mkdir()
    marker = runtime / isolated.OWNER_FILE

    def forbidden(*_args, **_kwargs):
        raise AssertionError("소유 기록이 잘못됐는데 자원 종료 명령을 실행했습니다.")

    monkeypatch.setattr(isolated.subprocess, "run", forbidden)
    for ports in ([], [1, 1, 2, 3], [1, 2, 3, "4"], [1, 2, 3, True],
                  [0, 2, 3, 4], [1, 2, 3, 65536]):
        marker.write_text(
            json.dumps({"purpose": "sos-test", "instance": instance, "ports": ports}),
            encoding="utf-8",
        )
        assert isolated.owned_record(runtime, instance) is None
        assert isolated.main(["--recover-owned", instance]) != 0
    assert marker.is_file()


def test_sigkill_after_owner_creation_leaves_recoverable_marker(monkeypatch, tmp_path):
    """진짜 SIGKILL은 정리 핸들러를 실행하지 않으므로 소유 기록을 보존한다."""
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    script = (
        "import sys, time; from pathlib import Path; "
        "from run_isolated_e2e import create_owner; import run_isolated_e2e as iso; "
        "iso.RUNS=Path(sys.argv[1]); "
        "create_owner(sys.argv[2], [10001, 10002, 10003, 10004]); "
        "print('owned', flush=True); time.sleep(30)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(tmp_path), instance],
        cwd=source.parent,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "owned"
        proc.kill()
        assert proc.wait(timeout=5) != 0
        runtime = tmp_path / instance
        assert (runtime / isolated.OWNER_FILE).is_file()

        def succeed(_command, **_kwargs):
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(isolated.subprocess, "run", succeed)
        assert isolated.main(["--recover-owned", instance]) == 0
        assert not runtime.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

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
