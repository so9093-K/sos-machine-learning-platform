"""오류 응답의 기존 detail 호환, 상관관계, 비밀 보호를 검증한다."""

from fastapi.testclient import TestClient

from sos_platform.api import create_app
from sos_platform.store import Store

from .support import upload


def test_validation_error_has_stable_code_and_request_id(workspace):
    client, _ = workspace
    response = client.post("/api/jobs", json={"kind": "not-a-job"})
    assert response.status_code == 422
    item = response.json()
    assert item["code"] == "SOS-API-VALIDATION"
    assert isinstance(item["detail"], list)
    assert item["request_id"] == response.headers["X-Request-ID"]
    assert item["retry_policy"] == "never"


def test_created_job_correlates_to_request_and_audit(workspace):
    client, store = workspace
    dataset = upload(client)
    reply = client.post("/api/jobs", json={"dataset_id": dataset["id"], "kind": "profile"})
    assert reply.status_code == 202
    job = reply.json()
    assert job["request_id"] == reply.headers["X-Request-ID"]
    assert store.get_job(job["id"])["request_id"] == job["request_id"]
    events = store.audit_events(limit=100)
    assert any(
        event["action"] == "job.create" and event["request_id"] == job["request_id"]
        for event in events
    )


def test_unexpected_failure_does_not_expose_exception_message(workspace, monkeypatch):
    client, store = workspace
    sentinel = "secret-password-exception-do-not-leak"

    def failure(_store, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(Store, "datasets", failure)
    with TestClient(create_app(store.home), raise_server_exceptions=False) as safe:
        response = safe.get("/api/datasets")
    assert response.status_code == 500
    assert response.json()["code"] == "SOS-API-INTERNAL"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert sentinel not in response.text


def test_denied_request_keeps_auth_header_and_request_id(workspace, monkeypatch):
    client, store = workspace
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        '{"' + ("a" * 64) + '":{"actor_id":"alice","kind":"human","roles":["viewer"]}}',
    )
    with TestClient(create_app(store.home)) as secured:
        denied = secured.get("/api/datasets")
    assert denied.status_code == 401
    assert denied.headers["WWW-Authenticate"] == "Bearer"
    assert denied.json()["request_id"] == denied.headers["X-Request-ID"]
    assert denied.json()["code"] == "SOS-AUTH-REQUIRED"

def test_unknown_http_routes_and_methods_use_same_problem_contract(workspace):
    client, _ = workspace
    for response in (
        client.get("/api/does-not-exist"),
        client.put("/api/datasets"),
    ):
        assert response.status_code in (404, 405)
        assert response.json()["code"] == (
            "SOS-API-NOT-FOUND" if response.status_code == 404 else "SOS-API-METHOD-NOT-ALLOWED"
        )
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        if response.status_code == 405:
            assert response.headers["allow"]


def test_audit_filter_finds_older_request_and_preserves_permissions(workspace, monkeypatch):
    client, store = workspace
    store.record_audit(
        actor_id="operator", actor_type="human", action="audit.find-me",
        resource_type="job", resource_id="abcdefab-cdef-abcd-efab-cdefabcdefab",
        request_id="12345678-1234-1234-1234-123456789abc",
    )
    for number in range(60):
        store.record_audit(
            actor_id="operator", actor_type="human", action="audit.noise",
            resource_type="job", resource_id=str(number),
        )
    found = client.get("/api/audit", params={"request_id": "12345678-1234-1234-1234-123456789abc"})
    assert found.status_code == 200
    assert len(found.json()) == 1 and found.json()[0]["action"] == "audit.find-me"
    by_job = client.get(
        "/api/audit", params={"job_id": "abcdefab-cdef-abcd-efab-cdefabcdefab"}
    )
    assert by_job.status_code == 200 and len(by_job.json()) == 1
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        '{"' + ("a" * 64) + '":{"actor_id":"viewer","kind":"human","roles":["viewer"]}}',
    )
    with TestClient(create_app(store.home)) as protected:
        denied = protected.get(
            "/api/audit", params={"request_id": "12345678-1234-1234-1234-123456789abc"}
        )
    assert denied.status_code == 401
