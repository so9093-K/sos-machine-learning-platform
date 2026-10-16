"""오류 응답의 기존 detail 호환, 상관관계, 비밀 보호를 검증한다."""

from fastapi.testclient import TestClient

from sos_platform.api import create_app
from sos_platform.store import Store
from sos_platform.worker import _perform

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

    monkeypatch.setattr(Store, "dataset_page", failure)
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
        actor_id="operator",
        actor_type="human",
        action="audit.find-me",
        resource_type="job",
        resource_id="abcdefab-cdef-abcd-efab-cdefabcdefab",
        request_id="12345678-1234-1234-1234-123456789abc",
    )
    for number in range(60):
        store.record_audit(
            actor_id="operator",
            actor_type="human",
            action="audit.noise",
            resource_type="job",
            resource_id=str(number),
        )
    found = client.get("/api/v2/audit", params={"request_id": "12345678-1234-1234-1234-123456789abc"})
    assert found.status_code == 200
    assert len(found.json()) == 1 and found.json()[0]["action"] == "audit.find-me"
    by_job = client.get("/api/v2/audit", params={"job_id": "abcdefab-cdef-abcd-efab-cdefabcdefab"})
    assert by_job.status_code == 200 and len(by_job.json()) == 1
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        '{"' + ("a" * 64) + '":{"actor_id":"viewer","kind":"human","roles":["viewer"]}}',
    )
    with TestClient(create_app(store.home)) as protected:
        denied = protected.get(
            "/api/v2/audit", params={"request_id": "12345678-1234-1234-1234-123456789abc"}
        )
    assert denied.status_code == 401


def test_worker_precommit_and_commit_failures_have_distinct_contracts(
    workspace, monkeypatch, tmp_path
):
    """실제 Append 진입을 기준으로 결과 불확실성을 분리한다."""
    import json

    import sos_platform.worker as worker

    client, store = workspace
    dataset = upload(client)
    posted = client.post(
        "/api/iceberg/import", json={"dataset_id": dataset["id"], "table_name": "diagnostic_case"}
    )
    assert posted.status_code == 202
    job = store.claim()
    assert job["id"] == posted.json()["id"]
    completed = tmp_path / "diagnosed.json"
    pending = tmp_path / "pending.parquet"
    secret = "password=internal-only-do-not-record"

    def fail_precommit(_store, _dataset, _table, _attempt, *, on_commit_start):
        raise RuntimeError(secret)

    monkeypatch.setattr(worker, "import_to_iceberg", fail_precommit)
    _perform(store.home, store.database_url, job, completed, pending)
    before = json.loads(completed.read_text())
    assert before["error_code"] == "SOS-JOB-FAILED"
    assert before["failure_stage"] == "iceberg_prepare"
    assert before["failure_category"] == "unclassified"
    assert before["exception_type"] == "RuntimeError"
    assert secret not in completed.read_text()

    def fail_after_commit_entry(_store, _dataset, _table, _attempt, *, on_commit_start):
        on_commit_start()
        raise RuntimeError(secret)

    monkeypatch.setattr(worker, "import_to_iceberg", fail_after_commit_entry)
    _perform(store.home, store.database_url, job, completed, pending)
    after = json.loads(completed.read_text())
    assert after["error_code"] == "SOS-ICEBERG-UNCERTAIN"
    assert after["failure_stage"] == "iceberg_commit"
    assert secret not in completed.read_text()


def test_operator_diagnostics_retains_category_without_raw_exception(workspace, monkeypatch):
    """운영자만 원인 종류를 조회하며 Job·Attempt·감사 상관관계가 이어진다."""
    from sos_platform.object_store import ObjectStore
    from sos_platform.worker import run_once

    client, store = workspace
    dataset = upload(client)
    posted = client.post(
        "/api/jobs",
        json={
            "dataset_id": dataset["id"],
            "kind": "transform",
            "transform": {"steps": [{"op": "fill_null", "column": "temperature", "value": 0}]},
        },
    )
    assert posted.status_code == 202
    job_id = posted.json()["id"]
    request_id = posted.headers["X-Request-ID"]
    secret = "private-token-that-must-not-appear"

    def fail_write(_store, _key, _path):
        raise RuntimeError(secret)

    monkeypatch.setattr(ObjectStore, "put", fail_write)
    assert run_once(store)
    details = client.get(f"/api/v2/diagnostics/jobs/{job_id}")
    assert details.status_code == 200
    payload = details.json()
    assert payload["request_id"] == request_id
    assert payload["error_code"] == "SOS-JOB-FAILED"
    assert payload["failure_stage"] == "publication"
    assert payload["failure_category"] == "unclassified"
    assert payload["exception_type"] == "RuntimeError"
    assert payload["attempts"][0]["failure_category"] == "unclassified"
    assert payload["attempts"][0]["exception_type"] == "RuntimeError"
    assert secret not in details.text
    audit = client.get("/api/v2/audit", params={"request_id": request_id})
    assert audit.status_code == 200
    assert {"job.create", "job.finish"} <= {row["action"] for row in audit.json()}
    public = client.get(f"/api/jobs/{job_id}")
    assert public.status_code == 200
    assert secret not in public.text
    assert "exception_type" not in public.json()


def test_invalid_bearer_token_length_preserves_challenge(workspace, monkeypatch):
    import hashlib
    import json

    client, store = workspace
    key = "real-valid-token-" + ("x" * 40)
    token_hash = hashlib.sha256(key.encode()).hexdigest()
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps({token_hash: {"actor_id": "operator", "kind": "human", "roles": ["operator"]}}),
    )
    with TestClient(create_app(store.home)) as secure:
        response = secure.get("/api/v2/audit", headers={"Authorization": "Bearer short"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        blocked = secure.get(
            "/api/v2/diagnostics/jobs/" + "a" * 36, headers={"Authorization": "Bearer short"}
        )
        assert blocked.status_code == 401
