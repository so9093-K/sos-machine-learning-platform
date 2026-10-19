"""E2E 격리 실행기가 기존 개발 구성과 데이터를 건드리지 않는지 검증한다."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "isolated_compose.py"
spec = importlib.util.spec_from_file_location("sos_isolated_e2e", SCRIPT)
assert spec is not None and spec.loader is not None
isolated = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = isolated
spec.loader.exec_module(isolated)


def test_isolated_e2e_rejects_non_test_instance():
    with pytest.raises(ValueError, match="Invalid isolated"):
        isolated.isolated_environment({}, "sos-platform", [1, 2, 3, 4])


def test_isolated_e2e_never_reuses_parent_data_or_auth():
    source = {
        "SOS_DATABASE_URL": "postgresql://user:secret@production/prod",
        "SOS_S3_ENDPOINT": "https://production.example",
        "SOS_AUTH_MODE": "token",
        "SOS_AUTH_TOKEN_HASHES": "production-credentials",
        "SOS_DB_NAME": "production",
        "SOS_S3_BUCKET": "production-data",
        "SOS_INFRA_MODE": "external",
        "COMPOSE_ENV_FILES": "/tmp/foreign.env",
        "COMPOSE_FILE": "/tmp/foreign-compose.yaml",
        "COMPOSE_DISABLE_ENV_FILE": "0",
        "SOS_JUNIT_FILE": "/tmp/workspace-e2e.xml",
    }
    isolated_env = isolated.isolated_environment(
        source, "sos-e2e-012345abcdef", [19003, 19005, 19002, 19001]
    )
    assert isolated_env["SOS_JUNIT_FILE"] == "/tmp/workspace-e2e.xml"
    assert "SOS_DATABASE_URL" not in isolated_env
    assert isolated_env["SOS_DB_NAME"] == "sos_e2e_012345abcdef"
    assert isolated_env["SOS_S3_BUCKET"] == "sos-e2e-012345abcdef"
    assert "SOS_AUTH_MODE" not in isolated_env
    assert isolated_env["SOS_AUTH_TOKEN_HASHES"] != "production-credentials"
    assert "SOS_E2E_ADMIN_TOKEN" in isolated_env
    identities = json.loads(isolated_env["SOS_AUTH_TOKEN_HASHES"])
    assert "administrator" in {actor["roles"][0] for actor in identities.values()}
    assert isolated_env["COMPOSE_PROJECT_NAME"] == "sos-e2e-012345abcdef"
    assert isolated_env["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert "COMPOSE_ENV_FILES" not in isolated_env
    assert "COMPOSE_FILE" not in isolated_env
    assert isolated_env["SOS_DB_USER"] == "sos"
    assert isolated_env["SOS_S3_REGION"] == "us-east-1"
    assert "SOS_S3_ENDPOINT" not in isolated_env


def test_cleanup_rejects_missing_or_foreign_owner(tmp_path):
    instance = "sos-e2e-123456abcdef"
    env = {"COMPOSE_PROJECT_NAME": instance}
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
    assert isolated.clean_owned(instance, runtime, {"COMPOSE_PROJECT_NAME": instance}) != 0
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
    assert isolated.clean_owned(instance, runtime, {"COMPOSE_PROJECT_NAME": instance}) == 0
    assert not runtime.exists()
    assert (foreign / "important.txt").read_text(encoding="utf-8") == "keep"
    command = calls[0]
    assert command[:2] == ["docker", "compose"]
    assert command[command.index("-p") + 1] == instance
    assert "down" in command
    assert "--volumes" in command
    assert [call for call in calls if call[:3] == ["docker", "image", "rm"]] == [
        ["docker", "image", "rm", f"sos-python-e2e:{instance}"],
        ["docker", "image", "rm", f"sos-web-e2e:{instance}"],
    ]


def test_cleanup_timeout_never_removes_owned_marker(monkeypatch, tmp_path):
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = isolated.create_owner(instance, [10001, 10002, 10003, 10004])

    def timeout(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 240)

    monkeypatch.setattr(isolated.subprocess, "run", timeout)
    assert isolated.clean_owned(instance, runtime, {"COMPOSE_PROJECT_NAME": instance}) != 0
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
    for ports in (
        [],
        [1, 1, 2, 3],
        [1, 2, 3, "4"],
        [1, 2, 3, True],
        [0, 2, 3, 4],
        [1, 2, 3, 65536],
    ):
        marker.write_text(
            json.dumps({"purpose": "sos-test", "instance": instance, "ports": ports}),
            encoding="utf-8",
        )
        assert isolated.owned_record(runtime, instance) is None
        assert isolated.main(["--recover-owned", instance]) != 0
    assert marker.is_file()


def test_token_mode_generates_only_ephemeral_roles():
    env = {}
    isolated.configure_tokens(env)
    identities = json.loads(env["SOS_AUTH_TOKEN_HASHES"])
    assert {record["roles"][0] for record in identities.values()} == {
        "viewer",
        "researcher",
        "operator",
        "administrator",
    }
    assert "ci-viewer" in {record["actor_id"] for record in identities.values()}
    assert all(len(key) == 64 for key in identities)


def test_cleanup_image_removal_failure_keeps_owner_record(monkeypatch, tmp_path):
    """이미지 정리가 실패하면 소유 증거를 보존해 복구 재시도를 허용한다."""
    instance = "sos-e2e-123456abcdef"
    monkeypatch.setattr(isolated, "RUNS", tmp_path)
    runtime = isolated.create_owner(instance, [10001, 10002, 10003, 10004])

    def fail_image(command, **_kwargs):
        failed = command[:3] == ["docker", "image", "rm"]
        return SimpleNamespace(returncode=1 if failed else 0)

    monkeypatch.setattr(isolated.subprocess, "run", fail_image)
    assert isolated.clean_owned(instance, runtime, {"COMPOSE_PROJECT_NAME": instance}) != 0
    assert (runtime / isolated.OWNER_FILE).is_file()
