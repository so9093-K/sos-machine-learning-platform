"""SOS 플랫폼 서비스의 실행·진단 인터페이스. 별도 Python 패키지가 필요하지 않다."""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".sos-dev"
PIDFILE = RUNTIME / "processes.json"
LOGDIR = RUNTIME / "logs"
PROCESS_NAMES = ("api", "worker", "web")
COMMANDS = ("up", "down", "restart", "status", "logs", "doctor", "check", "help")
EXIT_CONFIG, EXIT_FAILED = 2, 3


class StartupError(Exception):
    def __init__(self, code: str, message: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(message)


def configuration() -> dict[str, str]:
    """쉘 환경변수가 .env 파일보다 우선한다. 값을 출력하지 않는다."""
    values = {}
    source = ROOT / ".env"
    if source.is_file():
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    for key, val in (
        ("SOS_WEB_PORT", "9601"), ("SOS_API_PORT", "9602"),
        ("SOS_DB_PORT", "9603"), ("SOS_S3_PORT", "9605"),
        ("SOS_S3_CONSOLE_PORT", "9606"), ("SOS_DB_USER", "sos"),
        ("SOS_DB_PASSWORD", "sos-dev-only"), ("SOS_DB_NAME", "sos"),
        ("SOS_S3_ACCESS_KEY", "sos-dev"),
        ("SOS_S3_SECRET_KEY", "sos-dev-secret"),
        ("SOS_S3_BUCKET", "sos-data"),
        ("SOS_INFRA_MODE", "managed"),
    ):
        values.setdefault(key, val)
    values.setdefault("SOS_S3_ENDPOINT", f"http://127.0.0.1:{values['SOS_S3_PORT']}")
    return values


def run(command: list[str], *, env=None, cwd=ROOT, timeout=90) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartupError("SOS-CMD-001", "필수 명령을 실행하지 못했습니다.", str(exc)) from exc


def compose(args: list[str], env: dict[str, str]):
    return run(["docker", "compose", "-f", "compose.yaml", "-p", "sos-platform",
                *args], env=env, timeout=180)


def process_birth(pid: int) -> str | None:
    """PID 재사용으로 타 프로세스를 종료하지 않도록 시작 정보를 함께 확인한다."""
    try:
        if os.name == "posix" and Path(f"/proc/{pid}/stat").exists():
            raw = Path(f"/proc/{pid}/stat").read_text()
            return raw.rsplit(") ", 1)[1].split()[19]
        result = run(["ps", "-p", str(pid), "-o", "lstart="])
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, ValueError, StartupError):
        return None


def processes() -> dict:
    try:
        return json.loads(PIDFILE.read_text()) if PIDFILE.is_file() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def owned(record: dict | None) -> bool:
    if not record:
        return False
    try:
        pid = int(record["pid"])
        return pid > 0 and record.get("birth") == process_birth(pid) and (
            os.name != "posix" or os.getpgid(pid) == pid)
    except (OSError, KeyError, ValueError, TypeError):
        return False


def persist(state: dict):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    temporary = PIDFILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(PIDFILE)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except OSError:
        return False


def http_ok(url: str) -> tuple[bool, dict | None]:
    try:
        with urlopen(url, timeout=2) as response:
            content = response.read(8192)
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeDecodeError):
                payload = None
            return response.status == 200, payload
    except (HTTPError, URLError, TimeoutError, OSError):
        return False, None


def preflight(config: dict, *, need_docker: bool = True):
    required = ["uv", "node", "npm"]
    if need_docker:
        required.append("docker")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise StartupError("SOS-ENV-001", "필수 실행 도구가 없습니다.",
                           ", ".join(missing))
    ports = ["SOS_WEB_PORT", "SOS_API_PORT"]
    if config["SOS_INFRA_MODE"] == "managed":
        ports += ["SOS_DB_PORT", "SOS_S3_PORT", "SOS_S3_CONSOLE_PORT"]
        if run(["docker", "compose", "version"]).returncode:
            raise StartupError("SOS-ENV-002", "Docker Compose를 사용할 수 없습니다.")
    try:
        numbers = [int(config[name]) for name in ports]
    except ValueError as exc:
        raise StartupError("SOS-CONFIG-001", "설정된 포트가 숫자가 아닙니다.") from exc
    if len(set(numbers)) != len(numbers) or any(p < 1 or p > 65535 for p in numbers):
        raise StartupError("SOS-CONFIG-002", "포트 번호가 중복되거나 유효하지 않습니다.")
    state = processes()
    for name, port_key in (("api", "SOS_API_PORT"), ("web", "SOS_WEB_PORT")):
        if port_open(int(config[port_key])) and not owned(state.get(name)):
            raise StartupError("SOS-NET-001", f"{name} 포트를 다른 프로세스가 사용합니다.",
                               f"포트 {config[port_key]}가 이미 사용 중입니다.")
    if config["SOS_INFRA_MODE"] not in ("managed", "external"):
        raise StartupError("SOS-CONFIG-003", "SOS_INFRA_MODE는 managed 또는 external이어야 합니다.")


def status(config: dict) -> dict:
    state = processes()
    web_port, api_port = config["SOS_WEB_PORT"], config["SOS_API_PORT"]
    checks = []
    web_ready, _ = http_ok(f"http://127.0.0.1:{web_port}/")
    api_ready, details = http_ok(f"http://127.0.0.1:{api_port}/api/ready")
    db_tcp = port_open(int(config["SOS_DB_PORT"])) if config["SOS_INFRA_MODE"] == "managed" else None
    s3_tcp = port_open(int(config["SOS_S3_PORT"])) if config["SOS_INFRA_MODE"] == "managed" else None
    checks.append({"name": "database", "status": "reachable" if db_tcp else
                   ("unreachable" if db_tcp is False else "external")})
    checks.append({"name": "storage", "status": "reachable" if s3_tcp else
                   ("unreachable" if s3_tcp is False else "external")})
    for name, ready in (("api", api_ready), ("worker", bool(details and
            details.get("services", {}).get("worker") == "ready")), ("web", web_ready)):
        record = state.get(name)
        checks.append({"name": name, "status": "ready" if ready and owned(record)
                       else "running" if owned(record) else "stopped"})
    return {"operation": "status", "environment": config["SOS_INFRA_MODE"],
            "status": "ready" if all(x["status"] in ("ready", "reachable", "external")
                                    for x in checks) and api_ready else "unavailable",
            "services": checks, "diagnostics": [],
            "urls": {"web": f"http://localhost:{web_port}",
                     "api": f"http://localhost:{api_port}",
                     "docs": f"http://localhost:{api_port}/docs"}}


def start_process(name: str, command: list[str], env: dict, cwd: Path = ROOT):
    state = processes()
    if owned(state.get(name)):
        return
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with (LOGDIR / f"{name}.log").open("ab") as output:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=True)
    # SIGKILL 대상 검증에는 PID 외에 시작 시점까지 보관한다.
    for _ in range(20):
        birth = process_birth(proc.pid)
        if birth:
            break
        time.sleep(0.05)
    if not birth:
        raise StartupError("SOS-PROC-001", f"{name} 프로세스가 시작되지 않았습니다.")
    state[name] = {"pid": proc.pid, "birth": birth}
    persist(state)


def stop_process(name: str):
    state = processes()
    record = state.get(name)
    if not owned(record):
        state.pop(name, None)
        persist(state)
        return
    pid = int(record["pid"])
    os.killpg(pid, signal.SIGTERM)
    for _ in range(30):
        if not owned(record):
            break
        time.sleep(0.1)
    if owned(record):
        os.killpg(pid, signal.SIGKILL)
    state.pop(name, None)
    persist(state)


def stop_services():
    for name in ("web", "api", "worker"):
        stop_process(name)


def wait_ready(config: dict, timeout: int = 90):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        result = status(config)
        if result["status"] == "ready":
            return result
        if any(not owned(processes().get(name)) for name in PROCESS_NAMES):
            raise StartupError("SOS-PROC-002", "서비스 프로세스가 예기치 않게 종료됐습니다.",
                               "make logs로 해당 서비스 로그를 확인하세요.")
        time.sleep(1)
    raise StartupError("SOS-READY-001", "서비스 준비 시간이 초과됐습니다.",
                       "make doctor 및 make logs로 준비 상태를 확인하세요.")


def up(config: dict, *, initialize: bool = True):
    start = time.monotonic()
    preflight(config, need_docker=config["SOS_INFRA_MODE"] == "managed")
    env = dict(config)
    if config["SOS_INFRA_MODE"] == "managed":
        # Compose 명령은 인프라 프로세스만 관리하며 볼륨을 삭제하지 않는다.
        result = compose(["up", "-d", "--wait"], env)
        if result.returncode:
            raise StartupError("SOS-INFRA-001", "인프라 시작에 실패했습니다.",
                               "make logs 또는 docker compose ps로 상태를 확인하세요.")
    if initialize:
        for command, code, label in (
            (["uv", "sync", "--locked", "--group", "dev"], "SOS-ENV-003", "Python 의존성 설치"),
            (["npm", "ci", "--no-audit"], "SOS-ENV-004", "웹 의존성 설치"),
            (["uv", "run", "--locked", "alembic", "upgrade", "head"], "SOS-DB-001", "DB 마이그레이션"),
            (["uv", "run", "--locked", "python", "-m", "sos_platform.bootstrap"],
             "SOS-STORAGE-001", "스토리지 초기화"),
        ):
            result = run(command, env=env, cwd=ROOT / "web" if command[0] == "npm" else ROOT,
                         timeout=240)
            if result.returncode:
                raise StartupError(code, f"{label}에 실패했습니다.",
                                   "설정·연결·의존성을 확인하고 make doctor를 실행하세요.")
    start_process("api", ["uv", "run", "--locked", "uvicorn", "sos_platform.api:app",
                          "--host", "127.0.0.1", "--port", config["SOS_API_PORT"]], env)
    start_process("worker", ["uv", "run", "--locked", "python", "-m",
                             "sos_platform.worker"], env)
    start_process("web", ["npm", "run", "dev", "--", "--host", "127.0.0.1",
                          "--port", config["SOS_WEB_PORT"], "--strictPort"], env, ROOT / "web")
    result = wait_ready(config)
    result["operation"] = "up"
    result["duration_ms"] = round((time.monotonic() - start) * 1000)
    return result


def down(config: dict):
    stop_services()
    if config["SOS_INFRA_MODE"] == "managed":
        result = compose(["down"], config)
        if result.returncode:
            raise StartupError("SOS-INFRA-002", "인프라 종료에 실패했습니다.")
    return {"operation": "down", "status": "stopped", "services": [], "diagnostics": []}


def doctor(config: dict, deep: bool = False):
    results = []
    for name in ("uv", "node", "npm"):
        results.append({"check": name, "status": "passed" if shutil.which(name) else "failed",
                        "message": "실행 도구 검색"})
    if config["SOS_INFRA_MODE"] == "managed":
        results.append({"check": "docker", "status": "passed" if shutil.which("docker")
                        else "failed", "message": "컨테이너 실행 도구 검색"})
    for service, key in (("api_tcp", "SOS_API_PORT"), ("web_tcp", "SOS_WEB_PORT"),
                         ("database_tcp", "SOS_DB_PORT"), ("storage_tcp", "SOS_S3_PORT")):
        if config["SOS_INFRA_MODE"] == "external" and service.endswith("_tcp") and service in (
            "database_tcp", "storage_tcp"):
            continue
        connected = port_open(int(config[key]))
        results.append({"check": service, "status": "passed" if connected else "failed",
                        "message": "설정된 호스트·포트의 TCP 연결 결과"})
    if deep:
        ready, details = http_ok(f"http://127.0.0.1:{config['SOS_API_PORT']}/api/ready")
        results.append({"check": "api_readiness", "status": "passed" if ready else "failed",
                        "message": "API의 실제 의존성 준비 상태",
                        "details": details})
    return {"operation": "doctor", "environment": config["SOS_INFRA_MODE"],
            "status": "passed" if all(r["status"] == "passed" for r in results)
            else "failed", "diagnostics": results, "services": []}


def logs(config: dict):
    for name in PROCESS_NAMES:
        file = LOGDIR / f"{name}.log"
        print(f"\n--- {name} ---")
        if not file.is_file():
            print("기록된 로그가 없습니다.")
            continue
        lines = file.read_text(errors="replace").splitlines()[-70:]
        for line in lines:
            # 인증값·연결 문자열을 터미널 출력에서 제거한다.
            line = re.sub(r"(?i)(password|secret|token|access_key)([=:]\s*)([^\s,;]+)",
                          r"\1\2[REDACTED]", line)
            line = re.sub(r"(?i)(postgresql(?:\+\w+)?://)[^@\s]+@",
                          r"\1[REDACTED]@", line)
            print(line)
    if config["SOS_INFRA_MODE"] == "managed":
        print("\n인프라 상세 로그는 관리 대상 서비스의 Docker 로그에서 확인합니다.")


def check(config: dict):
    state = status(config)
    if state["status"] != "ready":
        raise StartupError("SOS-CHECK-001", "통합 검증 전에 플랫폼이 Ready 상태여야 합니다.",
                           "make up을 먼저 실행하세요.")
    env = dict(config)
    commands = [
        (["uv", "run", "--locked", "ruff", "check", "."], ROOT),
        (["uv", "run", "--locked", "ruff", "format", "--check", "."], ROOT),
        (["uv", "run", "--locked", "pytest", "-q"], ROOT),
        (["npm", "run", "build"], ROOT / "web"),
        (["npm", "run", "test"], ROOT / "web"),
        (["npx", "playwright", "test"], ROOT / "web"),
    ]
    for command, cwd in commands:
        print("검증:", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=cwd, env=env, check=False)
        if result.returncode:
            raise StartupError("SOS-CHECK-002", "품질·통합 검증에 실패했습니다.",
                               f"실패한 검사: {command[0]}")
    return {"operation": "check", "status": "passed", "services": [],
            "diagnostics": []}


def help_text():
    print("SOS Platform · 개발자 명령")
    for name, desc in (
        ("up", "전체 서비스 실행 및 Readiness 확인"),
        ("down", "관리 대상 종료 · 데이터와 볼륨 보존"),
        ("restart", "서비스만 재시작 · DB 초기화 없음"),
        ("status", "구성요소별 실행 및 준비 상태"),
        ("logs", "서비스 로그 확인"),
        ("doctor", "읽기 전용 환경 및 연결 진단"),
        ("check", "코드 품질·테스트 통합 검증 (make up 선행)"),
        ("help", "사용 가능한 명령 표시"),
    ):
        print(f"  make {name:<10} {desc}")


def render(result: dict, format_: str):
    if format_ == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"SOS PLATFORM · {result.get('environment', 'development').upper()}")
    print(f"Operation: {result.get('operation', '')} · {result.get('status', '')}")
    for item in result.get("services", []):
        print(f"  {item['name']:<12} {item['status']}")
    for item in result.get("diagnostics", []):
        print(f"  {item['check']:<20} {item['status']} · {item['message']}")
    for label, url in result.get("urls", {}).items():
        print(f"  {label}: {url}")
    if result.get("error_code"):
        print(f"Error Code: {result['error_code']}")


def main():
    parser = argparse.ArgumentParser(description="SOS Platform Development CLI")
    parser.add_argument("command", choices=COMMANDS, nargs="?", default="help")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args()
    if args.command == "help":
        help_text()
        return 0
    config = configuration()
    try:
        if args.command == "up":
            result = up(config)
        elif args.command == "down":
            result = down(config)
        elif args.command == "restart":
            stop_services()
            result = up(config, initialize=False)
            result["operation"] = "restart"
        elif args.command == "status":
            result = status(config)
        elif args.command == "doctor":
            result = doctor(config, deep=args.deep)
        elif args.command == "logs":
            logs(config)
            return 0
        elif args.command == "check":
            result = check(config)
        else:
            raise AssertionError(args.command)
        render(result, args.format)
        return 0 if result["status"] in ("ready", "passed", "stopped") else EXIT_FAILED
    except StartupError as exc:
        result = {"operation": args.command, "environment": config["SOS_INFRA_MODE"],
                  "status": "failed", "error_code": exc.code,
                  "diagnostics": [{"check": "operation", "status": "failed",
                                   "message": str(exc), "possible_cause": exc.detail,
                                   "recommendation": "make doctor 및 make logs를 확인하세요."}],
                  "services": status(config)["services"]}
        render(result, args.format)
        return EXIT_CONFIG if exc.code.startswith(("SOS-ENV", "SOS-CONFIG", "SOS-NET")) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
