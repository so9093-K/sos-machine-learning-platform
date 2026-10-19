"""신원·권한·감사·CORS 통합 계약을 검증한다."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from sos_platform.api import create_app
from sos_platform.store import Store

from .support import upload


@pytest.mark.pr
def test_identity_roles_ownership_and_audit(workspace, monkeypatch):
    """Bearer 신원·기본 거부·리소스 소유권·변경 감사가 API에서 강제된다."""
    client, store = workspace
    dataset = upload(client)

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
        assert secure.get("/api/v2/auth/me", headers=headers("alice")).json()["actor_id"] == "alice"
        assert secure.get("/api/storage/inspection", headers=headers("alice")).status_code == 403
        assert secure.get("/api/v2/audit", headers=headers("alice")).status_code == 403
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
            headers={**headers("alice"), "Idempotency-Key": "alice-import-001"},
        )
        assert allowed_import.status_code == 202, allowed_import.text
        # 신규 Iceberg Dataset 연결은 생성 권한을 통과한 다음 테이블을 검증한다.
        connect_missing = secure.post(
            "/api/datasets/iceberg",
            json={"table_name": "nonexistent"},
            headers=headers("alice"),
        )
        assert connect_missing.status_code == 404
        assert secure.get(f"/api/datasets/{owned}", headers=headers("bob")).status_code == 404
        assert secure.get(f"/api/datasets/{owned}", headers=headers("alice")).status_code == 200
        # 신규 범용 Query도 기존 Dataset의 소유권을 그대로 적용한다.
        assert (
            secure.post(
                f"/api/datasets/{owned}/query", json={"limit": 1}, headers=headers("alice")
            ).status_code
            == 200
        )
        assert (
            secure.post(
                f"/api/datasets/{owned}/query", json={"limit": 1}, headers=headers("bob")
            ).status_code
            == 404
        )
        assert secure.post(f"/api/datasets/{owned}/query", json={"limit": 1}).status_code == 401
        assert secure.get("/api/datasets", headers=headers("bob")).json() == {
            "items": [],
            "next_cursor": None,
        }
        assert (
            secure.post(
                "/api/datasets/files",
                files={"file": ("no.csv", b"a\n1\n", "text/csv")},
                headers=headers("bob"),
            ).status_code
            == 403
        )
        missing_key = secure.post(
            "/api/jobs",
            json={"dataset_id": owned, "kind": "profile"},
            headers=headers("alice"),
        )
        assert missing_key.status_code == 428
        assert missing_key.json()["code"] == "SOS-JOB-IDEMPOTENCY-REQUIRED"
        job = secure.post(
            "/api/jobs",
            json={"dataset_id": owned, "kind": "profile"},
            headers={**headers("alice"), "Idempotency-Key": "alice-profile-001"},
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

        exploration_body = {
            "dataset_id": owned,
            "exploration": {"column": "a", "metric": "count"},
        }
        missing_discovery_key = secure.post(
            "/api/explorations/jobs", json=exploration_body, headers=headers("alice")
        )
        assert missing_discovery_key.status_code == 428
        assert missing_discovery_key.json()["code"] == "SOS-JOB-IDEMPOTENCY-REQUIRED"
        exploration = secure.post(
            "/api/explorations/jobs",
            json=exploration_body,
            headers={**headers("alice"), "Idempotency-Key": "alice-explore-001"},
        )
        assert exploration.status_code == 202, exploration.text
        exploration_id = exploration.json()["id"]
        alice_detail = secure.get(
            f"/api/explorations/jobs/{exploration_id}", headers=headers("alice")
        )
        bob_detail = secure.get(f"/api/explorations/jobs/{exploration_id}", headers=headers("bob"))
        general_detail = secure.get(f"/api/jobs/{exploration_id}", headers=headers("alice"))
        bob_list = secure.get("/api/explorations/jobs", headers=headers("bob"))
        bob_cancel = secure.post(
            f"/api/explorations/jobs/{exploration_id}/cancel", headers=headers("bob")
        )
        alice_cancel = secure.post(
            f"/api/explorations/jobs/{exploration_id}/cancel", headers=headers("alice")
        )
        assert alice_detail.status_code == 200
        assert bob_detail.status_code == 404
        assert general_detail.status_code == 404
        assert bob_list.json()["items"] == []
        assert bob_cancel.status_code == 403
        assert alice_cancel.json()["status"] == "Cancelled"
        history = secure.get("/api/v2/audit", headers=headers("ops"))
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


@pytest.mark.parametrize(
    "header",
    ("Forwarded", "X-Forwarded-For", "X-Real-IP", "X-Forwarded-Host", "Via"),
)
def test_development_identity_rejects_forwarded_requests(workspace, header):
    """로컬 프록시 전달 흔적이 있으면 개발용 관리자 신원을 거부한다."""
    client, _ = workspace
    assert client.get("/api/v2/auth/me").json()["actor_id"] == "local-developer"
    response = client.get("/api/datasets", headers={header: "external-client"})
    assert response.status_code == 403


def test_slow_denied_audit_does_not_block_other_requests(workspace, monkeypatch):
    """거부 감사가 느려도 다른 API 요청을 이벤트 루프에서 처리한다."""
    client, _ = workspace
    entered = Event()
    release = Event()
    original = Store.record_audit

    def slow_audit(self, **kwargs):
        entered.set()
        if not release.wait(timeout=20):
            raise TimeoutError("Test audit gate was not released")
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "record_audit", slow_audit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        denied = executor.submit(
            client.get, "/api/v2/auth/me", headers={"X-Forwarded-For": "198.51.100.9"}
        )
        try:
            assert entered.wait(timeout=10), "Denied audit did not start"
            health = executor.submit(client.get, "/api/health")
            assert health.result(timeout=3).status_code == 200
        finally:
            release.set()
        assert denied.result(timeout=20).status_code == 403


@pytest.mark.pr
def test_denied_audit_failure_does_not_grant_access(workspace, monkeypatch):
    """거부 요청의 감사 DB 기록 실패가 접근 허용으로 바뀌지 않는다."""
    _, store = workspace

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
