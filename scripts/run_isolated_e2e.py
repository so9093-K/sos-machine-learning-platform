"""E2E 쓰기를 실제 개발 데이터에서 분리해 실행한다.

고유 프로젝트·포트·PostgreSQL DB·S3 볼륨·프로세스 PID 기록을 생성한다.
기존 기본 SOS Compose 프로젝트에는 down/볼륨 정리를 절대 실행하지 않는다.
"""

import os
import re
import secrets
import shutil
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTANCE_PATTERN = re.compile(r"sos-e2e-[0-9a-f]{12}\Z")


def free_ports(count: int = 4) -> list[int]:
    """루프백에서 일시 예약한 서로 다른 4개 포트를 고른다."""
    reserved = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            reserved.append(sock)
        return [sock.getsockname()[1] for sock in reserved]
    finally:
        for sock in reserved:
            sock.close()


def isolated_environment(source: dict[str, str], instance: str, ports: list[int]) -> dict:
    """부모의 외부 스토리지·사용자 권한 설정을 격리 환경에 전달하지 않는다."""
    if not INSTANCE_PATTERN.fullmatch(instance) or len(set(ports)) != 4:
        raise ValueError("Invalid isolated E2E project or ports")
    env = dict(source)
    for key in (
        "SOS_DATABASE_URL",
        "SOS_DB_HOST",
        "SOS_S3_ENDPOINT",
        "SOS_AUTH_TOKEN_HASHES",
    ):
        env.pop(key, None)
    db_port, s3_port, api_port, web_port = ports
    env.update(
        SOS_DEV_INSTANCE=instance,
        SOS_INFRA_MODE="managed",
        SOS_AUTH_MODE="development",
        SOS_DB_NAME=instance.replace("-", "_"),
        SOS_S3_BUCKET=instance,
        SOS_DB_PORT=str(db_port),
        SOS_S3_PORT=str(s3_port),
        SOS_API_PORT=str(api_port),
        SOS_WEB_PORT=str(web_port),
        SOS_S3_ENDPOINT=f"http://127.0.0.1:{s3_port}",
        SOS_HOME=str(ROOT / ".sos-dev" / "runs" / instance / "home"),
    )
    return env


def main() -> int:
    instance = "sos-e2e-" + secrets.token_hex(6)
    env = isolated_environment(os.environ, instance, free_ports())
    # 부모 인스턴스의 구성·프로세스·볼륨과 별도이며 별도 프로젝트의 자원만 제거한다.
    runtime = ROOT / ".sos-dev" / "runs" / instance
    try:
        subprocess.run(["python3", "scripts/dev.py", "up"], cwd=ROOT, env=env, check=True)
        subprocess.run(
            ["npx", "playwright", "test", "workspace.spec.ts"],
            cwd=ROOT / "web",
            env=env,
            check=True,
        )
        return 0
    finally:
        subprocess.run(["python3", "scripts/dev.py", "down"], cwd=ROOT, env=env, check=False)
        # 관리 대상이 정확한 UUID E2E 프로젝트인 경우에만 자신이 만든 볼륨을 정리한다.
        if INSTANCE_PATTERN.fullmatch(instance):
            clean = subprocess.run(
                ["docker", "compose", "-f", "compose.yaml", "-p", instance, "down", "--volumes"],
                cwd=ROOT,
                env=env,
                check=False,
            )
            if clean.returncode == 0:
                shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
