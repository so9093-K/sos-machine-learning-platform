"""문서에 공개한 API 상태·인증 계약이 실제 HTTP 동작과 일치하는지 확인한다."""

import hashlib
from types import SimpleNamespace


from fastapi.testclient import TestClient

from sos_platform import api_control
from sos_platform.api import create_app
from sos_platform.security import Actor, IdentitySettings
from sos_platform.store import Store


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


def test_model_authoring_is_separate_from_general_execution(tmp_path, monkeypatch):
    """일반 실행 토큰이 직접 API로 학습·정의 변경을 우회하지 못한다."""
    actors = {
        "analysis-token-with-enough-entropy": Actor("owner", "human", frozenset({"researcher"})),
        "automation-token-with-enough-entropy": Actor("owner", "service", frozenset({"automation"})),
    }
    identity = IdentitySettings("token", {
        hashlib.sha256(token.encode()).hexdigest(): actor for token, actor in actors.items()
    })
    source = {"id": "source", "version": "v1", "kind": "file", "name": "input",
              "location": "s3://test/source.parquet", "owner_actor": "owner"}
    submitted = []
    monkeypatch.setattr(Store, "get_dataset", lambda self, identifier: source if identifier == "source" else None)
    monkeypatch.setattr(Store, "get_job", lambda self, identifier: None)
    monkeypatch.setattr(Store, "get_model_revision", lambda self, identifier: None)

    def create_job(self, **values):
        submitted.append(values)
        return {**values, "id": "job-1", "status": "Queued", "result": None,
                "error": None, "error_code": None, "cancel_requested": False,
                "created_at": "2026-10-29T00:00:00Z", "updated_at": "2026-10-29T00:00:00Z",
                "attempts": []}

    monkeypatch.setattr(Store, "create_job", create_job)
    client = TestClient(create_app(tmp_path, identity=identity))
    analyst, automation = [{"Authorization": "Bearer " + token, "Idempotency-Key": "authoring-test-01"}
                        for token in actors]
    train = {"dataset_id": "source", "features": ["f1"]}
    definition = {"feature_dataset_id": "source", "entity_column": "host",
                  "timestamp_column": "event_time", "features": ["f1"]}
    for path, body in [
        ("/api/ml/train/jobs", train),
        ("/api/ml/train/jobs/missing/fork", {}),
        ("/api/pipelines/activity-anomaly-if/runs", {"dataset_id": "source"}),
        ("/api/features/services/jobs", definition),
        ("/api/features/services/missing/retirement/jobs", {}),
        ("/api/ml/registry/groups", {"registry_key": "test", "title": "test"}),
    ]:
        assert client.post(path, json=body, headers=analyst).status_code == 403, path
    assert submitted == []
    # 일반 실행·기록 조회는 제작 권한 변경의 영향을 받지 않는다.
    assert client.get("/api/ml/train/jobs/missing", headers=analyst).status_code == 404
    assert client.post("/api/ml/scoring/jobs", json={
        "dataset_id": "source", "model_revision_id": "missing",
    }, headers=analyst).status_code == 404
    assert client.post("/api/ml/train/jobs", json=train, headers=automation).status_code == 202
    assert submitted[-1]["kind"] == "ml_train"
    assert client.post("/api/features/services/jobs", json=definition, headers=automation).status_code == 202
    assert submitted[-1]["kind"] == "feature_register"
    capabilities = client.get("/api/v2/auth/me", headers=analyst).json()["capabilities"]
    assert "job.create" in capabilities
    assert not {"model.train", "feature.manage", "model.manage"}.intersection(capabilities)
