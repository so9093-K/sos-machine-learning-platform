"""신원·권한·감사·CORS 통합 계약을 검증한다."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from sos_platform.api import create_app
from sos_platform.store import Store
from .support import upload


def test_cors_uses_configured_web_port(workspace):
    client, _ = workspace
    response = client.options(
        "/api/datasets",
        headers={
            "Origin": "http://localhost:9601",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:9601"


def test_identity_roles_ownership_and_audit(workspace, monkeypatch):
    """Bearer 신원·기본 거부·리소스 소유권·변경 감사가 API에서 강제된다."""
    client, store = workspace
    dataset = upload(client)
    from sos_platform.api import create_app

    users = {
        "alice": {"actor_id": "alice", "kind": "human", "roles": ["researcher"]},
        "bob": {"actor_id": "bob", "kind": "human", "roles": ["viewer"]},
        "ops": {"actor_id": "ops", "kind": "human", "roles": ["operator"]},
        "admin": {"actor_id": "admin", "kind": "human", "roles": ["administrator"]},
    }
    tokens = {name: ("sos-secure-" + name + "-" * 40) for name in users}
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(tokens[name].encode()).hexdigest(): user
                for name, user in users.items()
            }
        ),
    )
    with TestClient(create_app(store.home)) as secure:

        def headers(name):
            return {"Authorization": "Bearer " + tokens[name]}

        assert secure.get("/api/datasets").status_code == 401
        assert (
            secure.get("/api/datasets", headers={"Authorization": "Bearer bad"}).status_code == 401
        )
        assert secure.get("/api/auth/me", headers=headers("alice")).json()["actor_id"] == "alice"
        assert secure.get("/api/storage/inspection", headers=headers("alice")).status_code == 403
        assert secure.get("/api/audit", headers=headers("alice")).status_code == 403
        assert secure.get("/api/storage/policy", headers=headers("ops")).status_code == 200
        # Data 생성·실행과 이미 존재하는 Snapshot의 운영 보존은 서로 다른 권한이다.
        assert (
            secure.post(
                "/api/datasets/iceberg",
                json={"table_name": "nonexistent"},
                headers=headers("ops"),
            ).status_code
            == 403
        )
        assert (
            secure.post(
                "/api/iceberg/import",
                json={"dataset_id": dataset["id"], "table_name": "restricted"},
                headers=headers("ops"),
            ).status_code
            == 403
        )
        assert (
            secure.post(
                "/api/iceberg/import",
                json={"dataset_id": dataset["id"], "table_name": "restricted"},
                headers=headers("bob"),
            ).status_code
            == 403
        )
        assert (
            secure.post(
                "/api/jobs",
                json={"dataset_id": dataset["id"], "kind": "profile"},
                headers=headers("alice"),
            ).status_code
            == 404
        )
        result = secure.post(
            "/api/datasets/files",
            files={"file": ("user.csv", b"a,b\n1,2\n", "text/csv")},
            headers=headers("alice"),
        )
        assert result.status_code == 201, result.text
        owned = result.json()["id"]
        allowed_import = secure.post(
            "/api/iceberg/import",
            json={"dataset_id": owned, "table_name": "allowed_research"},
            headers=headers("alice"),
        )
        assert allowed_import.status_code == 202, allowed_import.text
        # 신규 Iceberg Dataset 연결은 생성 권한을 통과한 다음 테이블을 검증한다.
        connect_missing = secure.post(
            "/api/datasets/iceberg",
            json={"table_name": "nonexistent"},
            headers=headers("alice"),
        )
        assert connect_missing.status_code == 422
        assert secure.get(f"/api/datasets/{owned}", headers=headers("bob")).status_code == 404
        assert secure.get(f"/api/datasets/{owned}", headers=headers("alice")).status_code == 200
        assert secure.get("/api/datasets", headers=headers("bob")).json() == []
        assert (
            secure.post(
                "/api/datasets/files",
                files={"file": ("no.csv", b"a\n1\n", "text/csv")},
                headers=headers("bob"),
            ).status_code
            == 403
        )
        job = secure.post(
            "/api/jobs",
            json={"dataset_id": owned, "kind": "profile"},
            headers=headers("alice"),
        )
        assert job.status_code == 202, job.text
        assert (
            secure.get(f"/api/jobs/{job.json()['id']}", headers=headers("bob")).status_code == 404
        )
        assert secure.get("/api/jobs", headers=headers("bob")).json()["items"] == []
        assert (
            secure.post(f"/api/jobs/{job.json()['id']}/cancel", headers=headers("bob")).status_code
            == 403
        )
        cancelled = secure.post(f"/api/jobs/{job.json()['id']}/cancel", headers=headers("alice"))
        assert cancelled.status_code == 200, cancelled.text
        history = secure.get("/api/audit", headers=headers("ops"))
        assert history.status_code == 200
        events = history.json()
        assert any(x["action"] == "dataset.create" and x["actor_id"] == "alice" for x in events)
        assert any(x["action"] == "job.cancel" and x["actor_id"] == "alice" for x in events)
        assert any(x["action"] == "security.access_denied" for x in events)
        assert any(
            x["action"] == "security.resource_access_denied"
            and x["actor_id"] == "bob"
            and x["resource_id"] == owned
            for x in events
        )
        assert tokens["alice"] not in json.dumps(events)


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


def test_required_audit_failure_rolls_back_job_creation(workspace, monkeypatch):
    """중요 변경의 감사 기록을 확정하지 못하면 Job 생성도 커밋되지 않는다."""
    client, store = workspace
    source = upload(client)

    def fail_audit(_db, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(Store, "_audit", staticmethod(fail_audit))
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        store.create_job(
            kind="profile",
            dataset_id=source["id"],
            spec={},
            owner_actor="local-developer",
        )
    assert store.jobs() == []


def test_development_identity_rejects_forwarded_requests(workspace):
    """로컬 프록시로 전달된 외부 요청에 개발용 관리자 권한을 주지 않는다."""
    client, _ = workspace
    response = client.get("/api/datasets", headers={"X-Forwarded-For": "198.51.100.9"})
    assert response.status_code == 403


def test_denied_audit_failure_does_not_grant_access(workspace, monkeypatch):
    """거부 요청의 감사 DB 기록 실패가 접근 허용으로 바뀌지 않는다."""
    _, store = workspace
    from sos_platform.api import create_app

    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(b"secure-secret-" + b"x" * 40).hexdigest(): {
                    "actor_id": "reader",
                    "kind": "human",
                    "roles": ["viewer"],
                }
            }
        ),
    )

    def fail_audit(_store, **_kwargs):
        raise RuntimeError("Audit database unavailable")

    monkeypatch.setattr(Store, "record_audit", fail_audit)
    with TestClient(create_app(store.home)) as client:
        assert client.get("/api/datasets").status_code == 401
        assert client.post("/api/datasets/files").status_code == 401


def test_configured_cors_allows_token_preflight(workspace, monkeypatch):
    """설정 포트의 개발용 UI가 Bearer 요청을 보낼 수 있어야 한다."""
    _, store = workspace
    monkeypatch.setenv("SOS_WEB_PORT", "9765")
    with TestClient(create_app(store.home)) as app:
        response = app.options(
            "/api/datasets",
            headers={
                "Origin": "http://localhost:9765",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:9765"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
