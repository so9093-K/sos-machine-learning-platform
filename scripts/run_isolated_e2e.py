"""고유 DB·S3·Compose와 테스트 프로세스로 브라우저 검증을 격리한다.

일반 개발 인스턴스와 외부 저장소는 생성·정리 대상으로 취급하지 않는다.
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
RUNS = ROOT / ".sos-dev" / "runs"
INSTANCE_PATTERN = re.compile(r"sos-e2e-[0-9a-f]{12}\Z")
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
        "SOS_E2E_VIEWER_TOKEN",
        "SOS_E2E_RESEARCHER_TOKEN",
        "SOS_E2E_OPERATOR_TOKEN",
    ):
        env.pop(key, None)
    db_port, s3_port, api_port, web_port = ports
    env.update(
        SOS_DEV_INSTANCE=instance,
        SOS_INFRA_MODE="managed",
        SOS_AUTH_MODE="development",
        SOS_DB_NAME=instance.replace("-", "_"),
        SOS_DB_PASSWORD=secrets.token_urlsafe(24),
        SOS_S3_ACCESS_KEY="sos-test",
        SOS_S3_SECRET_KEY=secrets.token_urlsafe(24),
        SOS_S3_BUCKET=instance,
        SOS_DB_PORT=str(db_port),
        SOS_S3_PORT=str(s3_port),
        SOS_API_PORT=str(api_port),
        SOS_WEB_PORT=str(web_port),
        SOS_S3_ENDPOINT=f"http://127.0.0.1:{s3_port}",
        SOS_HOME=str(RUNS / instance / "home"),
    )
    return env


def configure_tokens(env: dict[str, str]) -> None:
    """테스트 전용 임시 신원을 생성한다. 원문은 출력·영속화하지 않는다."""
    roles = ("viewer", "researcher", "operator")
    identities = {}
    for role in roles:
        token = secrets.token_hex(32)
        env[f"SOS_E2E_{role.upper()}_TOKEN"] = token
        identities[hashlib.sha256(token.encode()).hexdigest()] = {
            "actor_id": f"ci-{role}",
            "kind": "human",
            "roles": [role],
        }
    env["SOS_AUTH_MODE"] = "token"
    env["SOS_AUTH_TOKEN_HASHES"] = json.dumps(identities)


def create_owner(instance: str, ports: list[int]) -> Path:
    """새 고유 실행 경로와 소유 증거를 먼저 만든다."""
    if not INSTANCE_PATTERN.fullmatch(instance):
        raise ValueError("Invalid isolated E2E project")
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
    ):
        return None
    return data


def clean_owned(instance: str, runtime: Path, env: dict[str, str]) -> int:
    """검증 소유 환경만 종료·제거하고 정리 실패를 호출자에게 전달한다."""
    if owned_record(runtime, instance) is None or env.get("SOS_DEV_INSTANCE") != instance:
        print("소유 증거가 없어 E2E 리소스를 정리하지 않습니다.", file=sys.stderr)
        return 1
    down = subprocess.run(["python3", "scripts/dev.py", "down"], cwd=ROOT, env=env, check=False)
    # 소유 프로젝트에만 --volumes를 적용한다. 기본 프로젝트에는 적용하지 않는다.
    containers = subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "-p", instance, "down", "--volumes"],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if down.returncode or containers.returncode:
        print(
            f"E2E 정리 실패: {instance} (서비스 {down.returncode}, Compose {containers.returncode})",
            file=sys.stderr,
        )
        return 1
    try:
        shutil.rmtree(runtime)
    except OSError as exc:
        print(f"E2E 로컬 실행 기록 정리 실패: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


def execute(spec: str, *, token_mode: bool = False) -> int:
    """한 번의 테스트 실행과 정리를 수행한다. 테스트 재시도는 하지 않는다."""
    instance = "sos-e2e-" + secrets.token_hex(6)
    ports = free_ports()
    env = isolated_environment(os.environ, instance, ports)
    if token_mode:
        configure_tokens(env)
    runtime = create_owner(instance, ports)
    result = 1
    try:
        subprocess.run(["python3", "scripts/dev.py", "up"], cwd=ROOT, env=env, check=True)
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
    parser.add_argument("--token-mode", action="store_true")
    parser.add_argument("--recover-owned")
    args = parser.parse_args(argv)
    if args.recover_owned:
        instance = args.recover_owned
        runtime = RUNS / instance
        data = owned_record(runtime, instance)
        if data is None or not isinstance(data.get("ports"), list):
            print("유효한 소유 기록을 확인하지 못했습니다.", file=sys.stderr)
            return 1
        env = isolated_environment(os.environ, instance, data["ports"])
        return clean_owned(instance, runtime, env)
    signal.signal(signal.SIGTERM, _interrupted)
    spec = args.spec or "workspace.spec.ts"
    if spec == "auth.spec.ts" and not args.token_mode:
        parser.error("auth.spec.ts에는 --token-mode가 필요합니다.")
    return execute(spec, token_mode=args.token_mode)


if __name__ == "__main__":
    raise SystemExit(main())
