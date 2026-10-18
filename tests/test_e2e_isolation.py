"""E2E 격리 실행기가 기존 개발 구성과 데이터를 건드리지 않는지 검증한다."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "isolated_compose.py"
spec = importlib.util.spec_from_file_location("sos_isolated_e2e", SCRIPT)
assert spec is not None and spec.loader is not None
isolated = importlib.util.module_from_spec(spec)
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
    assert "SOS_S3_ENDPOINT" not in isolated_env
