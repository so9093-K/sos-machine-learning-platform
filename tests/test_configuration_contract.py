"""실행 설정의 중복 표현이 달라지지 않도록 실제 진입점을 대조한다."""

import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from sos_platform.config import Settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG_KEYS = (
    "SOS_DB_PORT",
    "SOS_DB_USER",
    "SOS_DB_PASSWORD",
    "SOS_DB_NAME",
    "SOS_S3_PORT",
    "SOS_S3_ACCESS_KEY",
    "SOS_S3_SECRET_KEY",
    "SOS_S3_BUCKET",
)


def test_managed_defaults_match_compose_example_and_application(monkeypatch):
    """Compose·환경 예제·Settings의 데이터 서비스 기본값을 대조한다."""
    example = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("SOS_") and "=" in line:
            name, value = line.split("=", 1)
            example[name] = value

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    composed = {}
    for name, value in re.findall(r"\$\{(SOS_[A-Z0-9_]+):-([^}]+)\}", compose):
        composed.setdefault(name, set()).add(value)

    monkeypatch.delenv("SOS_S3_REGION", raising=False)
    for key in (*CONFIG_KEYS, "SOS_DATABASE_URL", "SOS_DB_HOST", "SOS_S3_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    for key in CONFIG_KEYS:
        assert key in example, f"Missing documented default: {key}"
        assert key in composed, f"Compose is missing shared default: {key}"
        assert composed[key] == {example[key]}, f"Conflicting Compose default: {key}"

    settings = Settings.from_env()
    database = urlsplit(settings.database_url)
    assert database.hostname == "127.0.0.1"
    assert database.port == int(example["SOS_DB_PORT"])
    assert unquote(database.username or "") == example["SOS_DB_USER"]
    assert unquote(database.password or "") == example["SOS_DB_PASSWORD"]
    assert database.path == f"/{example['SOS_DB_NAME']}"
    assert settings.s3_endpoint == f"http://127.0.0.1:{example['SOS_S3_PORT']}"
    assert settings.s3_access_key == example["SOS_S3_ACCESS_KEY"]
    assert settings.s3_secret_key == example["SOS_S3_SECRET_KEY"]
    assert settings.s3_bucket == example["SOS_S3_BUCKET"]
    assert settings.s3_region == example["SOS_S3_REGION"]


def test_api_requires_explicit_authentication_mode(monkeypatch, tmp_path):
    """인증 모드가 없으면 API 생성은 부수효과 없이 실패한다."""
    from sos_platform.api import create_app
    from sos_platform.security import IdentitySettings

    monkeypatch.delenv("SOS_AUTH_MODE", raising=False)
    with pytest.raises(ValueError, match="SOS_AUTH_MODE"):
        IdentitySettings.from_env()
    unused_home = tmp_path / "invalid-auth-home"
    with pytest.raises(ValueError, match="SOS_AUTH_MODE"):
        create_app(unused_home)
    assert not unused_home.exists()

    monkeypatch.setenv("SOS_AUTH_MODE", "")
    with pytest.raises(ValueError, match="SOS_AUTH_MODE"):
        IdentitySettings.from_env()


def test_protected_mode_rejects_invalid_credentials(monkeypatch):
    """토큰 모드는 누락·잘못된 식별자·무효 역할 설정을 기동 전에 거부한다."""
    from sos_platform.security import IdentitySettings

    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.delenv("SOS_AUTH_TOKEN_HASHES", raising=False)
    with pytest.raises(ValueError):
        IdentitySettings.from_env()
    monkeypatch.setenv("SOS_AUTH_TOKEN_HASHES", '{"bad": {"actor_id": "admin"}}')
    with pytest.raises(ValueError):
        IdentitySettings.from_env()


def test_openapi_export_has_no_authentication_environment_side_effects(
    monkeypatch, tmp_path, capsys
):
    """OpenAPI 생성은 인증 모드나 로컬 개발 저장소를 암묵적으로 설정하지 않는다."""
    spec = importlib.util.spec_from_file_location(
        "sos_test_export_openapi", ROOT / "scripts" / "export_openapi.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.delenv("SOS_AUTH_MODE", raising=False)
    monkeypatch.chdir(tmp_path)
    module.main()
    schema = json.loads(capsys.readouterr().out)
    assert "/api/v2/auth/me" in schema["paths"]
    assert "SOS_AUTH_MODE" not in os.environ
    assert not (tmp_path / ".sos").exists()


def test_container_compose_keeps_token_auth_and_host_ports_private():
    """Docker 네트워크를 개발 관리자 신원으로 간주하지 않는다."""
    source = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "SOS_AUTH_MODE: token" in source
    assert "SOS_AUTH_TOKEN_HASHES: ${SOS_AUTH_TOKEN_HASHES:-}" in source
    assert "SOS_AUTH_MODE: development" not in source
    assert '"127.0.0.1:${SOS_API_PORT:-9602}:9602"' in source
    assert '"127.0.0.1:${SOS_WEB_PORT:-9601}:9601"' in source
    assert "service_completed_successfully" in source
    assert "worker_state:/var/lib/sos" in source
    assert "api_state:/var/lib/sos" in source


def test_docker_build_excludes_test_runtime_state():
    """E2E 격리 실행 기록을 이미지 빌드 컨텍스트로 보내지 않는다."""
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".sos-test" in ignore
