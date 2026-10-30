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
    train = {
        "dataset_id": "source", "features": ["f1"],
        "workflow_source": ["submission", "training", "run"],
        "workflow_submission_id": "submission", "workflow_stage": "training",
    }
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
    assert "workflow_source" not in submitted[-1]
    assert "workflow_submission_id" not in submitted[-1]
    assert "workflow_stage" not in submitted[-1]
    assert not {"workflow_source", "workflow_submission_id", "workflow_stage"}.intersection(
        submitted[-1]["spec"]
    )
    assert client.post("/api/features/services/jobs", json=definition, headers=automation).status_code == 202
    assert submitted[-1]["kind"] == "feature_register"
    capabilities = client.get("/api/v2/auth/me", headers=analyst).json()["capabilities"]
    assert "job.create" in capabilities
    assert not {"model.train", "feature.manage", "model.manage"}.intersection(capabilities)


def test_offered_model_cross_actor_use_withdrawal_and_result_ownership(tmp_path, monkeypatch):
    """제공은 모델 사용만 허용하며 중단 후 신규 실행과 과거 결과를 구별한다."""
    from sos_platform import api_ml_models, api_ml_results

    actors = {
        "supplier-token-with-enough-entropy": Actor("supplier", "service", frozenset({"automation"})),
        "consumer-token-with-enough-entropy": Actor("consumer", "human", frozenset({"researcher"})),
        "other-supplier-with-enough-entropy": Actor("other", "service", frozenset({"automation"})),
    }
    identity = IdentitySettings("token", {
        hashlib.sha256(token.encode()).hexdigest(): actor for token, actor in actors.items()
    })
    supplier, consumer, other = [{"Authorization": f"Bearer {token}",
                                "Idempotency-Key": "offering-contract-01"} for token in actors]
    model = {"id": "model", "owner_actor": "supplier", "job_id": "training",
        "attempt_id": "attempt", "dataset_id": "training-data", "dataset_version": "v1",
        "algorithm": "isolation_forest", "location": "s3://test/model", "sha256": "abc",
        "features": ["a"], "params": {}, "training_score_summary": {},
        "runtime": {"feature_dtypes": ["Float64"]},
        "created_at": "2026-10-29T00:00:00Z", "lifecycle_state": "Active", "registry_version": "1"}
    offering = {}
    submitted = []
    monkeypatch.setattr(Store, "record_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(Store, "get_model_revision", lambda self, mid: model if mid == "model" else None)
    monkeypatch.setattr(Store, "get_model_offering", lambda self, mid: dict(offering) or None)
    monkeypatch.setattr(Store, "get_dataset", lambda self, did: {
        "id": did, "owner_actor": "consumer" if did == "user-data" else "supplier", "version": "v1"})
    monkeypatch.setattr(Store, "get_job", lambda self, jid: {
        "id": jid, "kind": "ml_train", "owner_actor": "supplier"})
    monkeypatch.setattr(api_ml_models.MLflowSettings, "from_env", lambda: object())
    monkeypatch.setattr(api_ml_models, "MLflowClient", lambda settings: object())
    monkeypatch.setattr(api_ml_results, "verify_registered_version", lambda *args: None)
    monkeypatch.setattr(api_ml_results, "discovery_columns", lambda store, dataset: {
        "columns": [{"name": "a", "dtype": "Float64"}]})

    def offer(self, mid, **values):
        offering.update(description=values["description"], offered_by=values["actor_id"],
                        offered_at="2026-10-29T00:00:00Z", withdrawn_at=None)
        return dict(offering)

    def withdraw(self, mid, **values):
        offering["withdrawn_at"] = "2026-10-29T01:00:00Z"
        return dict(offering)

    def listing(self, **kwargs):
        return [{"id": "model", "created_at": offering["offered_at"], "model": model,
                 "description": offering["description"], "offered_by": "supplier",
                 "offered_at": offering["offered_at"]}] if offering and not offering["withdrawn_at"] else []

    def create(self, **values):
        assert values["require_model_offering"] is True
        assert values["spec"]["model_owner_actor"] == "supplier"
        submitted.append(values)
        return {**values, "id": "score-job", "status": "Queued", "result": None,
            "cancel_requested": False, "error": None, "attempts": [],
            "created_at": model["created_at"], "updated_at": model["created_at"]}

    monkeypatch.setattr(Store, "offer_model", offer)
    monkeypatch.setattr(Store, "withdraw_model_offering", withdraw)
    monkeypatch.setattr(Store, "list_model_offerings", listing)
    monkeypatch.setattr(Store, "create_job", create)
    scoring = {"id": "scored", "owner_actor": "consumer", "job_id": "score-job",
        "attempt_id": "score-attempt", "model_revision_id": "model", "dataset_id": "user-data",
        "dataset_version": "v1", "location": "s3://test/scores", "sha256": "abc", "row_count": 2,
        "score_direction": "higher_is_more_anomalous", "feature_names": ["a"],
        "score_summary": {}, "ranking_metrics": None, "label_column": None,
        "created_at": model["created_at"]}
    monkeypatch.setattr(Store, "get_scoring_result", lambda self, sid: scoring)
    client = TestClient(create_app(tmp_path, identity=identity))
    path = "/api/ml/models/model/offering"
    body = {"dataset_id": "user-data", "model_revision_id": "model"}
    assert client.get("/api/ml/models/model", headers=consumer).status_code == 404
    assert client.put(path, json={"description": "설명"}, headers=consumer).status_code == 403
    assert client.put(path, json={"description": "설명"}, headers=other).status_code == 404
    assert client.put(path, json={"description": "목적·입력·한계"}, headers=supplier).json()["offered"] is True
    assert client.get("/api/ml/offered-models", headers=consumer).json()["items"][0]["model"]["id"] == "model"
    assert client.get("/api/ml/models/model", headers=consumer).status_code == 200
    assert client.get("/api/datasets/training-data", headers=consumer).status_code == 404
    assert client.get("/api/ml/train/jobs/training", headers=consumer).status_code == 404
    assert client.post("/api/ml/scoring/jobs", json=body, headers=consumer).status_code == 202
    assert client.delete(path, headers=consumer).status_code == 403
    assert client.delete(path, headers=supplier).json()["offered"] is False
    assert client.get("/api/ml/offered-models", headers=consumer).json()["items"] == []
    assert client.post("/api/ml/scoring/jobs", json=body, headers=consumer).status_code == 404
    assert len(submitted) == 1
    assert client.get("/api/ml/scoring/results/scored", headers=consumer).status_code == 200
    assert client.get("/api/ml/scoring/results/scored", headers=other).status_code == 404
    # 기존의 모순된 기록에서도 삭제 중인 모델을 제공 중으로 표시하지 않는다.
    model["lifecycle_state"] = "Deleting"
    offering["withdrawn_at"] = None
    assert client.get(path, headers=supplier).json()["offered"] is False
