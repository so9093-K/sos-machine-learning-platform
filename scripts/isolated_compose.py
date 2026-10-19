"""격리 Docker Compose 환경에서 브라우저 검증과 소유 자원 복구를 수행한다.

Playwright는 Native Runner로 호스트에서 실행하며 SOS 서비스만 컨테이너로 기동한다.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / ".sos-test" / "runs"
INSTANCE_PATTERN = re.compile(r"sos-e2e-[0-9a-f]{12}\Z")
IMAGE_PREFIX_PATTERN = re.compile(r"ci-[0-9]+-[0-9]+\Z")
OWNER_FILE = "validation-owner.json"


def free_ports(count: int = 4) -> list[int]:
    """루프백에서 일시 예약한 서로 다른 포트들을 고른다."""
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


def valid_ports(ports: object) -> bool:
    """복구 기록이 조작됐더라도 유효하지 않은 포트를 사용하지 않는다."""
    return (
        isinstance(ports, list)
        and len(ports) == 4
        and all(type(port) is int and 1 <= port <= 65535 for port in ports)
        and len(set(ports)) == 4
    )


def isolated_environment(source: dict[str, str], instance: str, ports: list[int]) -> dict:
    """부모의 외부 스토리지·사용자 권한 설정을 격리 환경에 전달하지 않는다."""
    if not INSTANCE_PATTERN.fullmatch(instance) or not valid_ports(ports):
        raise ValueError("Invalid isolated E2E project or ports")
    # 부모의 외부 DB·S3·권한·Compose 프로젝트 설정을 전혀 상속하지 않는다.
    env = {
        key: value
        for key, value in source.items()
        if not key.startswith("SOS_")
        and key
        not in {
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "COMPOSE_FILE",
            "COMPOSE_ENV_FILES",
            "COMPOSE_DISABLE_ENV_FILE",
        }
    }
    db_port, s3_port, api_port, web_port = ports
    env.update(
        COMPOSE_PROJECT_NAME=instance,
        COMPOSE_DISABLE_ENV_FILE="1",
        SOS_DB_USER="sos",
        SOS_DB_NAME=instance.replace("-", "_"),
        SOS_DB_PASSWORD=secrets.token_urlsafe(24),
        SOS_S3_ACCESS_KEY="sos-test",
        SOS_S3_SECRET_KEY=secrets.token_urlsafe(24),
        SOS_S3_REGION="us-east-1",
        SOS_S3_BUCKET=instance,
        SOS_DB_PORT=str(db_port),
        SOS_S3_PORT=str(s3_port),
        SOS_API_PORT=str(api_port),
        SOS_WEB_PORT=str(web_port),
        SOS_PYTHON_IMAGE=f"sos-python-e2e:{instance}",
        SOS_WEB_IMAGE=f"sos-web-e2e:{instance}",
    )
    # 공통 검증 CLI가 소유한 출력 위치만 Playwright에 전달한다.
    if source.get("SOS_JUNIT_FILE"):
        env["SOS_JUNIT_FILE"] = source["SOS_JUNIT_FILE"]
    # CI에서 미리 빌드한 이미지의 코드만 공유한다. 각 실행의 DB·버킷·볼륨은
    # 계속 분리하며 공유 이미지는 개별 프로젝트의 정리 대상에 포함하지 않는다.
    image_prefix = source.get("SOS_E2E_IMAGE_PREFIX")
    if image_prefix is not None:
        if not IMAGE_PREFIX_PATTERN.fullmatch(image_prefix):
            raise ValueError("Invalid prebuilt E2E image prefix")
        env["SOS_PYTHON_IMAGE"] = f"sos-python-e2e:{image_prefix}"
        env["SOS_WEB_IMAGE"] = f"sos-web-e2e:{image_prefix}"
    configure_tokens(env)
    return env


def configure_tokens(env: dict[str, str]) -> None:
    """테스트 전용 임시 신원을 생성한다. 원문은 출력·영속화하지 않는다."""
    roles = ("viewer", "researcher", "operator", "administrator")
    identities = {}
    for role in roles:
        token = secrets.token_hex(32)
        env[f"SOS_E2E_{'ADMIN' if role == 'administrator' else role.upper()}_TOKEN"] = token
        identities[hashlib.sha256(token.encode()).hexdigest()] = {
            "actor_id": f"ci-{role}",
            "kind": "human",
            "roles": [role],
        }
    env["SOS_AUTH_TOKEN_HASHES"] = json.dumps(identities)


def create_owner(instance: str, ports: list[int]) -> Path:
    """새 고유 실행 경로와 소유 증거를 먼저 만든다."""
    if not INSTANCE_PATTERN.fullmatch(instance) or not valid_ports(ports):
        raise ValueError("Invalid isolated E2E project or ports")
    runtime = RUNS / instance
    runtime.mkdir(parents=True, exist_ok=False)
    marker = runtime / OWNER_FILE
    marker.write_text(
        json.dumps({"purpose": "sos-test", "instance": instance, "ports": ports}),
        encoding="utf-8",
    )
    return runtime


def owned_record(runtime: Path, instance: str) -> dict | None:
    """소유 증거가 없거나 현재 프로젝트와 다르면 삭제를 거부한다."""
    if not INSTANCE_PATTERN.fullmatch(instance):
        return None
    marker = runtime / OWNER_FILE
    if marker.is_symlink() or not marker.is_file() or runtime.is_symlink():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("purpose") != "sos-test"
        or data.get("instance") != instance
        or not valid_ports(data.get("ports"))
        or runtime != RUNS / instance
        or RUNS.is_symlink()
    ):
        return None
    return data


def clean_owned(instance: str, runtime: Path, env: dict[str, str]) -> int:
    """소유 증거를 확인하고 서비스 종료 후 소유 볼륨만 정리한다.

    서비스 종료가 실패하면 살아 있는 프로세스의 저장소를 삭제하지 않으며
    소유 기록을 남겨 명시적인 복구를 허용한다.
    """
    if owned_record(runtime, instance) is None or env.get("COMPOSE_PROJECT_NAME") != instance:
        print("소유 증거가 없어 E2E 리소스를 정리하지 않습니다.", file=sys.stderr)
        return 1
    try:
        # 소유 기록과 정확히 일치하는 프로젝트의 볼륨만 제거한다.
        containers = subprocess.run(
            [
                "docker",
                "compose",
                "--profile",
                "app",
                "-f",
                "compose.yaml",
                "-p",
                instance,
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            cwd=ROOT,
            env=env,
            check=False,
            timeout=240,
        )
        if containers.returncode:
            print(f"E2E Compose 정리 실패: {instance}", file=sys.stderr)
            return 1
        # 임시 고유 태그는 Compose down --volumes의 삭제 대상이 아니다.
        # 프로젝트 이름에서 파생한 이미지 태그만 제거한다. 공유 이미지/기반 이미지는 건드리지 않는다.
        for image in (f"sos-python-e2e:{instance}", f"sos-web-e2e:{instance}"):
            inspection = subprocess.run(
                ["docker", "image", "inspect", image],
                check=False,
                capture_output=True,
                timeout=30,
            )
            if inspection.returncode:
                continue  # 초기 빌드 전에 실패한 실행에는 이미지가 없을 수 있다.
            removed = subprocess.run(
                ["docker", "image", "rm", image],
                check=False,
                timeout=120,
            )
            if removed.returncode:
                print(f"E2E 테스트 전용 이미지 정리 실패: {instance}", file=sys.stderr)
                return 1
        shutil.rmtree(runtime)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"E2E 소유 리소스 정리 오류: {instance} ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


def compose_up_command(instance: str, *, prebuilt: bool) -> list[str]:
    """명시적으로 미리 준비한 이미지가 있으면 빌드를 반복하지 않는다."""
    return [
        "docker",
        "compose",
        "--profile",
        "app",
        "-f",
        "compose.yaml",
        "-p",
        instance,
        "up",
        "--no-build" if prebuilt else "--build",
        "-d",
        "--wait",
    ]


def execute(spec: str) -> int:
    """한 번의 테스트 실행과 정리를 수행한다. 테스트 재시도는 하지 않는다."""
    instance = "sos-e2e-" + secrets.token_hex(6)
    ports = free_ports()
    env = isolated_environment(os.environ, instance, ports)
    runtime = create_owner(instance, ports)
    result = 1
    try:
        subprocess.run(
            compose_up_command(instance, prebuilt="SOS_E2E_IMAGE_PREFIX" in os.environ),
            cwd=ROOT,
            env=env,
            check=True,
            timeout=900,
        )
        subprocess.run(
            ["npx", "playwright", "test", spec],
            cwd=ROOT / "web",
            env=env,
            check=True,
        )
        result = 0
    except (OSError, subprocess.CalledProcessError, KeyboardInterrupt, InterruptedError):
        print(f"격리 E2E 실행 실패: {instance}", file=sys.stderr)
    finally:
        if clean_owned(instance, runtime, env):
            result = 1
    return result


def _interrupted(_signum: int, _frame: object) -> None:
    """일반적인 SIGTERM에서는 안전한 정리 경로로 이동한다."""
    raise InterruptedError("격리 E2E 실행 중단")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", choices=("workspace.spec.ts", "auth.spec.ts"))
    parser.add_argument("--recover-owned")
    args = parser.parse_args(argv)
    if args.recover_owned:
        instance = args.recover_owned
        runtime = RUNS / instance
        data = owned_record(runtime, instance)
        if data is None:
            print("유효한 소유 기록을 확인하지 못했습니다.", file=sys.stderr)
            return 1
        env = isolated_environment(os.environ, instance, data["ports"])
        return clean_owned(instance, runtime, env)
    signal.signal(signal.SIGTERM, _interrupted)
    spec = args.spec or "workspace.spec.ts"
    return execute(spec)


if __name__ == "__main__":
    raise SystemExit(main())
