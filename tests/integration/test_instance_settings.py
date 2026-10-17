"""인스턴스 표시명, 관리자 권한 및 변경 감사의 계약 검증."""

import hashlib
import json

from fastapi.testclient import TestClient

from sos_platform.api import create_app


def test_instance_display_name_persists_and_audits(workspace):
    client, store = workspace
    response = client.get("/api/instance")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Local Workspace"
    assert "environment" not in response.json()
    changed = client.put("/api/instance", json={"display_name": "  Research Lab  "})
    assert changed.status_code == 200, changed.text
    assert changed.json()["display_name"] == "Research Lab"
    assert store.instance_display_name() == "Research Lab"
    assert any(
        row["action"] == "instance.configure" and row["resource_type"] == "instance"
        for row in store.audit_events()
    )
    assert client.put("/api/instance", json={"display_name": "   "}).status_code == 422
    assert client.put("/api/instance", json={"display_name": "x" * 49}).status_code == 422
    assert store.instance_display_name() == "Research Lab"
    with TestClient(create_app(store.home)) as restarted:
        assert restarted.get("/api/instance").json()["display_name"] == "Research Lab"


def test_instance_display_name_only_admin_can_change(workspace, monkeypatch):
    _, store = workspace
    principals = {
        "viewer": {"actor_id": "viewer-1", "kind": "human", "roles": ["viewer"]},
        "researcher": {"actor_id": "researcher-1", "kind": "human", "roles": ["researcher"]},
        "operator": {"actor_id": "operator-1", "kind": "human", "roles": ["operator"]},
        "admin": {"actor_id": "admin-1", "kind": "human", "roles": ["administrator"]},
    }
    tokens = {k: ("sos-secret-" + k + "-" * 45) for k in principals}
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(tokens[name].encode()).hexdigest(): user
                for name, user in principals.items()
            }
        ),
    )
    with TestClient(create_app(store.home)) as secure:
        for name in ("viewer", "researcher", "operator"):
            headers = {"Authorization": "Bearer " + tokens[name]}
            assert secure.get("/api/instance", headers=headers).status_code == 200
            identity = secure.get("/api/v2/auth/me", headers=headers)
            assert identity.status_code == 200
            abilities = identity.json()["capabilities"]
            assert abilities == sorted(set(abilities))
            assert "instance.configure" not in abilities
            if name == "operator":
                assert "storage.inspect" in abilities
                assert "dataset.read" not in abilities
            else:
                assert "dataset.read" in abilities
                assert "storage.inspect" not in abilities
            response = secure.put(
                "/api/instance", json={"display_name": "Unauthorized"}, headers=headers
            )
            assert response.status_code == 403, response.text
        admin_headers = {"Authorization": "Bearer " + tokens["admin"]}
        success = secure.put(
            "/api/instance", json={"display_name": "AI Studio"}, headers=admin_headers
        )
        allowed = secure.get("/api/v2/auth/me", headers=admin_headers).json()["capabilities"]
        assert "instance.configure" in allowed
        assert "dataset.read" in allowed
        assert "storage.inspect" in allowed
        assert success.status_code == 200, success.text
        assert success.json() == {"display_name": "AI Studio"}
        assert secure.get("/api/instance", headers=admin_headers).json()["display_name"] == (
            "AI Studio"
        )
    assert store.instance_display_name() == "AI Studio"


def test_cross_origin_preflight_allows_authorized_instance_put(workspace):
    """API의 실제 PUT 라우트는 허용된 UI 출처에서 CORS 사전 요청을 통과한다."""
    client, _store = workspace
    result = client.options(
        "/api/instance",
        headers={
            "Origin": "http://localhost:9601",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert result.status_code == 200, result.text
    assert result.headers["access-control-allow-origin"] == "http://localhost:9601"
    assert "PUT" in result.headers["access-control-allow-methods"].split(", ")
