"""문서에 공개한 API 상태·인증 계약이 실제 HTTP 동작과 일치하는지 확인한다."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from fastapi.testclient import TestClient

from sos_platform import api_control
from sos_platform.api import create_app
from sos_platform.security import Actor, IdentitySettings
from sos_platform.store import Store
from sos_platform.store_administration import AdministrationStoreMixin


def test_openapi_health_ready_and_bearer_contract(tmp_path):
    app = create_app(tmp_path, identity=IdentitySettings("development", {}))
    schema = app.openapi()
    paths = schema["paths"]
    health = paths["/api/health"]["get"]
    ready = paths["/api/ready"]["get"]

    assert health["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthOut"
    }
    assert ready["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadyOut"
    }
    assert ready["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadyOut"
    }
    assert "security" not in health
    assert "security" not in ready
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert paths["/api/v2/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]
    assert paths["/api/ml/train/jobs"]["post"]["security"] == [{"HTTPBearer": []}]

    # 문서 그룹은 OpenAPI 태그를 중복·누락 없이 분류해야 한다.
    declared_tags = {tag["name"] for tag in schema["tags"]}
    grouped_tags = [
        tag for group in schema["x-tagGroups"] for tag in group["tags"]
    ]
    assert sorted(grouped_tags) == sorted(declared_tags)

    for path, methods in paths.items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation.get("summary"), (method, path)
            assert len(operation.get("tags", [])) == 1, (method, path)
            assert operation["tags"][0] in declared_tags
            if path in {"/api/health", "/api/ready"} or (
                path == "/api/branding" and method == "get"
            ):
                assert "security" not in operation, (method, path)
            else:
                assert operation.get("security") == [{"HTTPBearer": []}], (
                    method, path,
                )


def test_status_responses_and_token_permissions(tmp_path, monkeypatch):
    app = create_app(tmp_path / "development", identity=IdentitySettings("development", {}))
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}

    # Scalar가 실제 HTML을 제공하는지 확인한다. 브라우저 UI 조작 테스트는 아니다.
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    assert "/openapi.json" in docs.text
    # 문서의 핵심 스크립트는 외부 CDN이 아닌 자체 자산을 가리켜야 한다.
    assert "/docs/assets/scalar.js" in docs.text

    # 외부 저장소·Worker를 호출하지 않고 Ready의 두 가지 HTTP 계약만 확인한다.
    monkeypatch.setattr(Store, "initialize", lambda self: None)
    monkeypatch.setattr(Store, "worker_ready", lambda self: False)
    monkeypatch.setattr(
        api_control,
        "ObjectStore",
        lambda: SimpleNamespace(ready=lambda: None),
    )
    unavailable = client.get("/api/ready")
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "status": "unavailable",
        "services": {"database": "ready", "storage": "ready", "worker": "unavailable"},
    }
    monkeypatch.setattr(Store, "worker_ready", lambda self: True)
    ready = client.get("/api/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "services": {"database": "ready", "storage": "ready", "worker": "ready"},
    }

    token = "valid-test-token-with-enough-entropy-value"
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    identity = IdentitySettings(
        "token", {fingerprint: Actor("viewer-a", "human", frozenset({"viewer"}))}
    )
    token_client = TestClient(create_app(tmp_path / "token", identity=identity))
    assert token_client.get("/api/v2/auth/me").status_code == 401
    authorized = {"Authorization": f"Bearer {token}"}
    assert token_client.get("/api/v2/auth/me", headers=authorized).status_code == 200
    assert token_client.get("/api/storage/policy", headers=authorized).status_code == 403


def test_public_branding_and_admin_edit_contract(tmp_path, monkeypatch):
    """인증 전 브랜드 조회와 관리자 설정 변경의 경계를 확인한다."""
    brand = {"name": "SOS"}

    monkeypatch.setattr(Store, "brand_name", lambda self: brand["name"])

    def update_brand(self, value, *, actor_id, actor_type, request_id):
        assert actor_id == "admin-a"
        assert actor_type == "human"
        brand["name"] = value

    monkeypatch.setattr(Store, "set_brand_name", update_brand)
    viewer_token = "valid-viewer-token-with-enough-entropy"
    admin_token = "valid-admin-token-with-enough-entropy"
    subjects = {
        hashlib.sha256(viewer_token.encode()).hexdigest():
            Actor("viewer-a", "human", frozenset({"viewer"})),
        hashlib.sha256(admin_token.encode()).hexdigest():
            Actor("admin-a", "human", frozenset({"administrator"})),
    }
    client = TestClient(create_app(tmp_path, identity=IdentitySettings("token", subjects)))
    initial = client.get("/api/branding")
    assert initial.json() == {"brand_name": "SOS"}
    assert initial.headers["cache-control"] == "no-store"
    payload = {"brand_name": "연구 플랫폼"}
    assert client.put("/api/branding", json=payload).status_code == 401
    viewer = {"Authorization": f"Bearer {viewer_token}"}
    assert client.put("/api/branding", json=payload, headers=viewer).status_code == 403
    admin = {"Authorization": f"Bearer {admin_token}"}
    changed = client.put("/api/branding", json=payload, headers=admin)
    assert changed.status_code == 200
    assert changed.json() == payload
    assert client.get("/api/branding").json() == payload
    for invalid in ("   ", "A\u007fB", "A\nB", "x" * 49):
        assert client.put("/api/branding", json={"brand_name": invalid}, headers=admin).status_code == 422
    assert client.get("/api/branding").json() == payload
    assert client.get("/api/instance").status_code == 401


def test_instance_branding_storage_is_independent_and_audited():
    """실제 SQL 저장·감사·실패 시 롤백을 검증한다. PostgreSQL 기동 검사는 아니다."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as db:
        db.execute(text(
            "CREATE TABLE instance_settings("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)"
        ))
        db.execute(text(
            "CREATE TABLE audit_events("
            "id TEXT PRIMARY KEY, at TEXT NOT NULL, actor_id TEXT NOT NULL,"
            "actor_type TEXT NOT NULL, action TEXT NOT NULL, "
            "resource_type TEXT NOT NULL, resource_id TEXT,"
            "decision TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT,"
            "request_id TEXT)"
        ))

    class InMemoryStore(AdministrationStoreMixin):
        @contextmanager
        def connection(self):
            with engine.begin() as db:
                yield db

    store = InMemoryStore()
    assert store.brand_name() == "SOS"
    assert store.instance_display_name() == "Local Workspace"
    store.set_brand_name(
        "ACME AI Lab", actor_id="operator-1", actor_type="human", request_id=None
    )
    assert store.brand_name() == "ACME AI Lab"
    assert store.instance_display_name() == "Local Workspace"

    store.set_instance_display_name(
        "Research Team", actor_id="operator-1", actor_type="human", request_id=None
    )
    assert store.brand_name() == "ACME AI Lab"
    assert store.instance_display_name() == "Research Team"

    with engine.connect() as db:
        resources = db.execute(text(
            "SELECT resource_id FROM audit_events ORDER BY at, resource_id"
        )).scalars().all()
        assert sorted(resources) == ["brand_name", "display_name"]
        assert db.execute(text(
            "SELECT updated_by FROM instance_settings WHERE key='brand_name'"
        )).scalar_one() == "operator-1"

    # 설정 변경 이후 감사 기록이 실패하면 같은 트랜잭션 전체가 취소되어야 한다.
    with engine.begin() as db:
        db.execute(text(
            "CREATE TRIGGER fail_brand_audit BEFORE INSERT ON audit_events "
            "WHEN NEW.resource_id = 'brand_name' "
            "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
        ))
    with pytest.raises(DatabaseError):
        store.set_brand_name(
            "MUST NOT SAVE", actor_id="operator-1", actor_type="human", request_id=None
        )
    assert store.brand_name() == "ACME AI Lab"
    engine.dispose()
