"""개발 CLI의 상태·진단·안전한 종료 계약을 외부 프로세스 없이 검증한다."""

import importlib.util
from pathlib import Path

import pytest

# 프로젝트 루트를 Python import 경로에 추가할 필요 없이 실제 CLI 파일을 검증한다.
source = Path(__file__).resolve().parents[1] / "scripts" / "dev.py"
spec = importlib.util.spec_from_file_location("sos_dev_cli", source)
assert spec is not None and spec.loader is not None
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


@pytest.fixture
def config():
    return {
        "SOS_INFRA_MODE": "managed",
        "SOS_WEB_PORT": "9601",
        "SOS_API_PORT": "9602",
        "SOS_DB_PORT": "9603",
        "SOS_S3_PORT": "9605",
        "SOS_DB_PASSWORD": "secret-password",
        "SOS_S3_ACCESS_KEY": "sos-dev",
        "SOS_S3_SECRET_KEY": "long-storage-secret",
    }


def test_help_and_makefile_are_public_interface(capsys):
    dev.help_text()
    value = capsys.readouterr().out
    assert all(f"make {name}" in value for name in dev.COMMANDS)
    content = Path("Makefile").read_text()
    assert ".DEFAULT_GOAL := help" in content
    assert "python3 scripts/dev.py $@" in content


def test_database_tcp_only_is_not_ready(monkeypatch, config):
    monkeypatch.setattr(dev, "processes", lambda: {name: {"pid": 1} for name in dev.PROCESS_NAMES})
    monkeypatch.setattr(dev, "owned", lambda record: bool(record))
    monkeypatch.setattr(dev, "port_open", lambda _port: True)
    monkeypatch.setattr(
        dev,
        "http_ok",
        lambda url: (True, None) if url.endswith(":9601/") else (False, None),
    )
    outcome = dev.status(config)
    assert outcome["status"] == "unavailable"
    services = {item["name"]: item["status"] for item in outcome["services"]}
    assert services["database"] == "reachable"
    assert services["storage"] == "reachable"
    assert services["api"] == "running"


def test_ready_requires_all_dependencies(monkeypatch, config):
    monkeypatch.setattr(dev, "processes", lambda: {name: {"pid": 1} for name in dev.PROCESS_NAMES})
    monkeypatch.setattr(dev, "owned", lambda record: True)
    monkeypatch.setattr(dev, "port_open", lambda _port: True)

    def response(url):
        if url.endswith("/api/ready"):
            return True, {"services": {"database": "ready", "storage": "ready", "worker": "ready"}}
        return True, None

    monkeypatch.setattr(dev, "http_ok", response)
    assert dev.status(config)["status"] == "ready"


def test_doctor_reports_invalid_port_without_mutation(monkeypatch, tmp_path, config):
    monkeypatch.setattr(dev, "RUNTIME", tmp_path / "never-created")
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(dev, "port_open", lambda _port: False)
    config["SOS_API_PORT"] = "invalid"
    result = dev.doctor(config)
    assert result["status"] == "failed"
    assert any(item["check"] == "SOS_API_PORT" for item in result["diagnostics"])
    assert not (tmp_path / "never-created").exists()


def test_preflight_rejects_foreign_port(monkeypatch, config):
    config["SOS_INFRA_MODE"] = "external"
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(dev, "processes", lambda: {})
    monkeypatch.setattr(dev, "port_open", lambda port: port == 9601)
    with pytest.raises(dev.StartupError) as exc:
        dev.preflight(config, need_docker=False)
    assert exc.value.code == "SOS-NET-001"


def test_lifecycle_lock_rejects_concurrent_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(dev, "RUNTIME", tmp_path)
    with dev.lifecycle_lock():
        with pytest.raises(dev.StartupError) as exc:
            with dev.lifecycle_lock():
                pass
    assert exc.value.code == "SOS-LIFE-001"
    with dev.lifecycle_lock():
        pass


def test_down_does_not_delete_volumes_or_external_resources(monkeypatch, config):
    actions = []
    monkeypatch.setattr(dev, "stop_services", lambda: actions.append("stopped"))
    monkeypatch.setattr(
        dev,
        "compose",
        lambda arguments, _config: (
            actions.append(arguments) or type("Result", (), {"returncode": 0})()
        ),
    )
    result = dev.down(config)
    assert result["status"] == "stopped"
    assert ["down"] in actions
    assert all("-v" not in item for item in actions if isinstance(item, list))

    config["SOS_INFRA_MODE"] = "external"
    actions.clear()
    dev.down(config)
    assert actions == ["stopped"]


def test_restart_skips_initialization(monkeypatch, tmp_path, config):
    monkeypatch.setattr(dev, "RUNTIME", tmp_path)
    monkeypatch.setattr(dev, "configuration", lambda: config)
    monkeypatch.setattr(dev, "stop_services", lambda: None)
    events = []

    def fake_up(_config, *, initialize=True):
        events.append(initialize)
        return {
            "operation": "up",
            "environment": "managed",
            "status": "ready",
            "services": [],
            "diagnostics": [],
        }

    monkeypatch.setattr(dev, "up", fake_up)
    monkeypatch.setattr(dev, "render", lambda _result, _fmt: None)
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "restart"])
    assert dev.main() == 0
    assert events == [False]


def test_redaction_hides_configured_secrets(config):
    line = (
        "SOS_DB_PASSWORD=secret-password "
        "postgresql://sos:secret-password@localhost:9603 "
        "token=the-token"
    )
    hidden = dev.redact(line, config)
    assert "secret-password" not in hidden
    assert "the-token" not in hidden
    assert "REDACTED" in hidden


def test_existing_sqlite_data_is_not_modified_by_preflight(monkeypatch, tmp_path, config):
    config["SOS_INFRA_MODE"] = "external"
    legacy = tmp_path / ".sos" / "metadata.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"preserve-me")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(dev, "port_open", lambda _port: False)
    monkeypatch.setattr(dev, "processes", lambda: {})
    dev.preflight(config, need_docker=False)
    assert legacy.read_bytes() == b"preserve-me"


def test_503_readiness_exposes_component_states(monkeypatch, config):
    monkeypatch.setattr(dev, "processes", lambda: {name: {"pid": 1} for name in dev.PROCESS_NAMES})
    monkeypatch.setattr(dev, "owned", lambda record: True)
    monkeypatch.setattr(dev, "port_open", lambda _port: True)
    monkeypatch.setattr(
        dev,
        "http_ok",
        lambda url: (
            (
                False,
                {"services": {"database": "ready", "storage": "ready", "worker": "unavailable"}},
            )
            if url.endswith("/api/ready")
            else (True, None)
        ),
    )
    outcome = dev.status(config)
    services = {item["name"]: item["status"] for item in outcome["services"]}
    assert services["database"] == "ready"
    assert services["storage"] == "ready"
    assert services["worker"] == "running"
    assert outcome["status"] == "unavailable"


def test_partial_up_rolls_back_only_new_processes(monkeypatch, tmp_path, config):
    config["SOS_INFRA_MODE"] = "external"
    web = tmp_path / "web"
    web.mkdir()
    (web / "package-lock.json").write_text('{"lockfileVersion": 3}')
    (web / "node_modules").mkdir()
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(dev, "preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        dev, "run", lambda *_args, **_kwargs: type("Result", (), {"returncode": 0})()
    )
    running = {"api": {"pid": 1}}
    monkeypatch.setattr(dev, "processes", lambda: dict(running))
    monkeypatch.setattr(dev, "owned", lambda record: bool(record))
    monkeypatch.setattr(
        dev,
        "start_process",
        lambda name, *_args, **_kwargs: running.setdefault(name, {"pid": len(running) + 1}),
    )
    stopped = []
    monkeypatch.setattr(
        dev, "stop_process", lambda name: (stopped.append(name), running.pop(name, None))
    )
    monkeypatch.setattr(
        dev,
        "wait_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            dev.StartupError("SOS-READY-001", "준비 실패")
        ),
    )
    with pytest.raises(dev.StartupError):
        dev.up(config, initialize=False)
    assert set(stopped) == {"worker", "web"}
    assert running == {"api": {"pid": 1}}
