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


def test_real_port_collision_does_not_stop_foreign_listener(monkeypatch, config):
    """실제 루프백 포트 충돌은 실행 전에 거부하며 외부 Listener를 닫지 않는다."""
    import socket

    config["SOS_INFRA_MODE"] = "external"
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(dev, "processes", lambda: {})
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen(1)
        config["SOS_API_PORT"] = str(foreign.getsockname()[1])
        with pytest.raises(dev.StartupError) as exc:
            dev.preflight(config, need_docker=False)
        assert exc.value.code == "SOS-NET-001"
        assert foreign.fileno() >= 0


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


def test_operation_result_matches_public_command(monkeypatch, config, capsys, tmp_path):
    monkeypatch.setattr(dev, "RUNTIME", tmp_path)
    monkeypatch.setattr(dev, "configuration", lambda: config)
    monkeypatch.setattr(
        dev,
        "up",
        lambda _config: {
            "operation": "up",
            "environment": "managed",
            "status": "ready",
            "services": [],
            "diagnostics": [],
        },
    )
    monkeypatch.setattr(
        dev,
        "down",
        lambda _config: {
            "operation": "down",
            "environment": "managed",
            "status": "stopped",
            "services": [],
            "diagnostics": [],
        },
    )
    for action in ("up", "down"):
        monkeypatch.setattr(dev.sys, "argv", ["dev.py", action, "--format", "json"])
        assert dev.main() == 0
        assert __import__("json").loads(capsys.readouterr().out)["operation"] == action


def test_external_up_does_not_migrate_or_initialize(monkeypatch, config, tmp_path):
    config["SOS_INFRA_MODE"] = "external"
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "RUNTIME", tmp_path / "runtime")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package-lock.json").write_text('{"lockfileVersion":3}')
    (tmp_path / "web" / "node_modules").mkdir()
    monkeypatch.setattr(dev, "preflight", lambda *_args, **_kwargs: None)
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(dev, "run", fake_run)
    monkeypatch.setattr(dev, "processes", lambda: {})
    monkeypatch.setattr(dev, "start_process", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        dev,
        "wait_ready",
        lambda _config: {"operation": "up", "status": "ready", "services": [], "diagnostics": []},
    )
    dev.up(config)
    assert any("sos_platform.validate" in command for command in calls)
    assert not any("alembic" in command or "sos_platform.bootstrap" in command for command in calls)


@pytest.mark.parametrize(
    "overrides",
    [
        {"SOS_DATABASE_URL": "postgresql+psycopg2://user:pass@db.example.com/prod"},
        {"SOS_DB_HOST": "db.example.com"},
        {"SOS_S3_ENDPOINT": "https://storage.example.com/bucket"},
        {"SOS_S3_ENDPOINT": "http://127.0.0.1:9705"},
    ],
)
def test_managed_preflight_blocks_external_storage_before_any_commands(
    config, overrides, monkeypatch
):
    config.update(overrides)
    monkeypatch.setattr(dev.shutil, "which", lambda _name: "/usr/bin/tool")
    invoked = []

    def unexpected(command, **_kwargs):
        invoked.append(command)
        raise AssertionError("관리형 사전 검사 실패 후 명령을 실행하면 안 됩니다.")

    monkeypatch.setattr(dev, "run", unexpected)
    with pytest.raises(dev.StartupError) as exc:
        dev.preflight(config)
    assert exc.value.code == "SOS-CONFIG-004"
    assert invoked == []


def test_s3_endpoint_default_uses_configured_port(monkeypatch):
    from sos_platform.config import Settings

    monkeypatch.delenv("SOS_S3_ENDPOINT", raising=False)
    monkeypatch.setenv("SOS_S3_PORT", "9745")
    assert Settings.from_env().s3_endpoint == "http://127.0.0.1:9745"


def test_make_check_validates_full_contract_once(monkeypatch, config):
    """공통 Suite를 재사용해 전체 검증을 한 번씩 호출한다."""
    monkeypatch.setattr(dev, "status", lambda _config: {"status": "ready"})
    commands = []

    def fake_run(command, *, cwd, env, check):
        commands.append((command, cwd))
        assert check is False
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(dev.subprocess, "run", fake_run)
    assert dev.check(config)["status"] == "passed"
    suites = (
        "ruff",
        "python-unit",
        "python-integration",
        "frontend",
        "openapi-types",
        "wheel",
        "alembic",
        "workspace-e2e",
        "make-lifecycle",
        "token-rbac",
    )
    assert commands == [
        (["uv", "run", "--locked", "python", "scripts/verify.py", "run", suite], dev.ROOT)
        for suite in suites
    ]


def test_make_check_fast_is_infrastructure_independent(monkeypatch, config):
    """빠른 검증은 Ready 조회 없이 인프라 없는 Suite만 선택한다."""

    def refuse_ready(_config):
        raise AssertionError("Fast checks must not connect to running services")

    commands = []

    def fake_run(command, *, cwd, env, check):
        assert check is False
        commands.append((command, cwd))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(dev, "status", refuse_ready)
    monkeypatch.setattr(dev.subprocess, "run", fake_run)
    assert dev.check_fast(config)["status"] == "passed"
    assert commands == [
        (["uv", "run", "--locked", "python", "scripts/verify.py", "run", suite], dev.ROOT)
        for suite in ("ruff", "python-unit", "frontend")
    ]


def test_log_relay_preserves_service_working_directory(monkeypatch, tmp_path):
    """npm은 web/에서 실행하며 wrapper만 절대 스크립트 경로로 호출한다."""
    arguments = []

    class FakeProcess:
        pid = 19993

    def fake_popen(command, **kwargs):
        arguments.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(dev, "LOGDIR", tmp_path / "logs")
    monkeypatch.setattr(dev, "processes", lambda: {})
    monkeypatch.setattr(dev, "owned", lambda _record: False)
    monkeypatch.setattr(dev, "process_birth", lambda _pid: "fixed-birth")
    monkeypatch.setattr(dev, "persist", lambda _state: None)
    monkeypatch.setattr(dev.subprocess, "Popen", fake_popen)
    dev.start_process("web", ["npm", "run", "dev"], {}, cwd=dev.ROOT / "web")
    command, settings = arguments[0]
    assert settings["cwd"] == dev.ROOT / "web"
    assert command[1] == str(dev.ROOT / "scripts" / "log_capture.py")
    assert command[-3:] == ["npm", "run", "dev"]


def test_iceberg_adoption_cli_rejects_external_database_without_running(monkeypatch, config):
    config["SOS_INFRA_MODE"] = "external"
    monkeypatch.setattr(dev, "configuration", lambda: config)
    monkeypatch.setattr(dev, "status", lambda _config: {"services": []})
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "iceberg-ownership", "--table", "sensor"])
    results = []
    monkeypatch.setattr(dev, "render", lambda value, _format: results.append(value))
    monkeypatch.setattr(
        dev, "run", lambda *_args, **_kwargs: pytest.fail("CLI must not access external DB")
    )
    assert dev.main() != 0
    assert results[0]["error_code"] == "SOS-CONFIG-004"


def test_iceberg_adoption_cli_previews_without_apply(monkeypatch, tmp_path, config, capsys):
    monkeypatch.setattr(dev, "configuration", lambda: config)
    monkeypatch.setattr(dev, "RUNTIME", tmp_path)
    monkeypatch.setattr(dev, "validate_managed_targets", lambda _config: None)
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "iceberg-ownership", "--table", "sensor"])
    recorded = []

    def invoke(command, **_kwargs):
        recorded.append(command)
        return type("Result", (), {"returncode": 0, "stdout": '{"state":"candidate"}'})()

    monkeypatch.setattr(dev, "run", invoke)
    assert dev.main() == 0
    assert "--table" in recorded[0]
    assert "--apply" not in recorded[0]
    assert '"candidate"' in capsys.readouterr().out
