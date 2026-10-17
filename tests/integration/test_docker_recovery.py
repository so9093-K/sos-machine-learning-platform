"""실제 Docker 컨테이너가 실행 중인 E2E 소유 프로세스의 SIGKILL 복구를 검증한다."""

import importlib.util
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

source = Path(__file__).resolve().parents[2] / "scripts" / "run_isolated_e2e.py"
spec = importlib.util.spec_from_file_location("sos_docker_recovery_isolation", source)
assert spec is not None and spec.loader is not None
isolated = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = isolated
spec.loader.exec_module(isolated)
ROOT = isolated.ROOT
RUNS = isolated.RUNS


def docker(args: list[str], env: dict[str, str], *, timeout: int = 30) -> str:
    """Docker 결과를 확인하고 실패를 무시하지 않는다."""
    result = subprocess.run(
        ["docker", *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def test_sigkill_owned_compose_recovery_keeps_foreign_volume() -> None:
    """Docker 서비스 기동 후 소유 프로세스만 강제 종료하고 실제 볼륨 경계를 검증한다."""
    instance = "sos-e2e-" + secrets.token_hex(6)
    foreign = "sos-test-sentinel-" + secrets.token_hex(6)
    env = isolated.isolated_environment(os.environ, instance, isolated.free_ports())
    runtime = RUNS / instance
    compose_args = ["compose", "-f", "compose.yaml", "-p", instance]
    child_code = """
import json
import os
import signal
import subprocess
import sys
from run_isolated_e2e import ROOT, create_owner

instance = sys.argv[1]
ports = json.loads(sys.argv[2])
runtime = create_owner(instance, ports)
subprocess.run(
    ["docker", "compose", "-f", "compose.yaml", "-p", instance, "up", "-d", "--wait"],
    cwd=ROOT,
    env=os.environ,
    check=True,
    timeout=180,
    stdout=subprocess.DEVNULL,
)
(runtime / "docker-ready").touch()
signal.pause()
"""
    child = None
    foreign_created = False
    try:
        docker(["volume", "create", foreign], env)
        foreign_created = True
        ports = [
            int(env[key]) for key in ("SOS_DB_PORT", "SOS_S3_PORT", "SOS_API_PORT", "SOS_WEB_PORT")
        ]
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", child_code, instance, json.dumps(ports)],
            cwd=ROOT / "scripts",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 180
        while not (runtime / "docker-ready").is_file():
            if child.poll() is not None:
                raise AssertionError("격리 Docker 기동 프로세스가 준비 전 종료됐습니다.")
            if time.monotonic() > deadline:
                raise AssertionError("격리 Docker 기동 완료를 확인하지 못했습니다.")
            time.sleep(0.2)

        services = docker([*compose_args, "ps", "--services", "--status", "running"], env)
        assert set(services.splitlines()) == {"postgres", "storage"}
        owned_volumes = docker(
            ["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={instance}"],
            env,
        ).splitlines()
        assert len(owned_volumes) == 2
        assert isolated.owned_record(runtime, instance) is not None

        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
        assert isolated.owned_record(runtime, instance) is not None
        # SIGKILL 이후에도 Compose 서비스와 데이터 볼륨은 그대로 남아 있어야 한다.
        assert set(
            docker([*compose_args, "ps", "--services", "--status", "running"], env).splitlines()
        ) == {"postgres", "storage"}

        recovery = subprocess.run(
            [sys.executable, "scripts/run_isolated_e2e.py", "--recover-owned", instance],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=540,
        )
        assert recovery.returncode == 0, "E2E 소유 기록 기반 복구가 실패했습니다."
        assert not runtime.exists()
        assert not docker([*compose_args, "ps", "-a", "-q"], env)
        assert not docker(
            ["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={instance}"],
            env,
        )
        assert docker(["volume", "inspect", "--format", "{{.Name}}", foreign], env) == foreign
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if runtime.exists() and isolated.owned_record(runtime, instance) is not None:
            # 실패해도 복구를 한 번 시도하되, 성공하지 않으면 소유 기록을 보존한다.
            subprocess.run(
                [sys.executable, "scripts/run_isolated_e2e.py", "--recover-owned", instance],
                cwd=ROOT,
                env=env,
                check=False,
                timeout=540,
            )
        if foreign_created:
            docker(["volume", "rm", foreign], env)
