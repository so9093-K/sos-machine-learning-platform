"""실행 설정의 중복 표현이 달라지지 않도록 실제 진입점을 대조한다."""

import importlib.util
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


def _dev_module():
    """개발 명령의 기본값을 별도 스키마로 복제하지 않고 실제 모듈에서 읽는다."""
    spec = importlib.util.spec_from_file_location("sos_dev_settings", ROOT / "scripts" / "dev.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_defaults_match_compose_example_and_application(monkeypatch, tmp_path):
    """CLI·Compose·환경 예제·Settings가 같은 관리형 연결 대상으로 시작한다."""
    example = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("SOS_") and "=" in line:
            name, value = line.split("=", 1)
            example[name] = value

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    composed = {}
    for name, value in re.findall(r"\$\{(SOS_[A-Z0-9_]+):-([^}]+)\}", compose):
        composed.setdefault(name, set()).add(value)

    cli = _dev_module()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.delenv("SOS_S3_REGION", raising=False)
    for key in (*CONFIG_KEYS, "SOS_DATABASE_URL", "SOS_DB_HOST", "SOS_S3_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    defaults = cli.configuration()
    for key in CONFIG_KEYS:
        assert key in example, f"Missing documented default: {key}"
        assert key in composed, f"Compose is missing shared default: {key}"
        assert composed[key] == {example[key]}, f"Conflicting Compose default: {key}"
        assert defaults[key] == example[key], f"CLI default drift: {key}"

    settings = Settings.from_env()
    database = urlsplit(settings.database_url)
    assert database.hostname == "127.0.0.1"
    assert database.port == int(defaults["SOS_DB_PORT"])
    assert unquote(database.username or "") == defaults["SOS_DB_USER"]
    assert unquote(database.password or "") == defaults["SOS_DB_PASSWORD"]
    assert database.path == f"/{defaults['SOS_DB_NAME']}"
    assert settings.s3_endpoint == defaults["SOS_S3_ENDPOINT"]
    assert settings.s3_endpoint == f"http://127.0.0.1:{defaults['SOS_S3_PORT']}"
    assert settings.s3_access_key == defaults["SOS_S3_ACCESS_KEY"]
    assert settings.s3_secret_key == defaults["SOS_S3_SECRET_KEY"]
    assert settings.s3_bucket == defaults["SOS_S3_BUCKET"]
    assert settings.s3_region == example["SOS_S3_REGION"]


def test_api_requires_explicit_authentication_mode_but_dev_cli_keeps_local_default(
    monkeypatch, tmp_path
):
    """직접 API 기동은 명시적 인증을 요구하고 로컬 개발 CLI 기본값은 유지한다."""
    from sos_platform.api import create_app
    from sos_platform.security import IdentitySettings

    cli = _dev_module()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.delenv("SOS_AUTH_MODE", raising=False)
    with pytest.raises(ValueError, match="SOS_AUTH_MODE"):
        IdentitySettings.from_env()
    unused_home = tmp_path / "invalid-auth-home"
    with pytest.raises(ValueError, match="SOS_AUTH_MODE"):
        create_app(unused_home)
    assert not unused_home.exists()

    assert cli.configuration()["SOS_AUTH_MODE"] == "development"

    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    assert cli.configuration()["SOS_AUTH_MODE"] == "token"
    monkeypatch.setenv("SOS_AUTH_MODE", "")
    with pytest.raises(ValueError, match="SOS_AUTH_MODE"):
        IdentitySettings.from_env()
