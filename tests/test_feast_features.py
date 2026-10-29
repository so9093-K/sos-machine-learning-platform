"""등록 Feature 입력·권한·Job 응답의 중요한 계약만 빠르게 확인한다."""

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sos_platform.api_features import READ_JOB, feast_router
from sos_platform.feature_contracts import (
    FeatureDefinitionRequest,
    FeatureRetrievalRequest,
    feature_identity,
)
from sos_platform.schemas import JobOut, JobPageOut
from sos_platform.security import Actor


def sample(**overrides):
    params = {
        "feature_dataset_id": "source",
        "entity_column": "host_id",
        "timestamp_column": "event_time",
        "features": ["count_1h", "rate"],
    }
    return FeatureDefinitionRequest(**(params | overrides))


@pytest.mark.parametrize("overrides", [
    {"entity_column": "event_time"},
    {"features": ["count_1h", "count_1h"]},
    {"features": ["sos_observation_id"]},
    {"features": ["host_id"]},
    {"ttl_seconds": 0},
])
def test_invalid_feature_definition(overrides):
    with pytest.raises(ValidationError):
        sample(**overrides)


def test_definition_identity_is_stable_and_versioned():
    original = feature_identity(sample(), {"id": "source", "version": "v1"})
    assert original == feature_identity(sample(), {"id": "source", "version": "v1"})
    assert original != feature_identity(sample(), {"id": "source", "version": "v2"})
    assert original != feature_identity(sample(ttl_seconds=60), {"id": "source", "version": "v1"})


def test_retrieval_requires_registered_job():
    with pytest.raises(ValidationError):
        FeatureRetrievalRequest(entity_dataset_id="events", timestamp_column="event_time")


def test_feature_job_response_contract():
    payload = {
        "id": "job", "kind": "feature_register", "dataset_id": "source",
        "spec": {}, "status": "Succeeded", "cancel_requested": False,
        "result": {}, "error": None, "created_at": "2026-10-28T00:00:00+00:00",
        "updated_at": "2026-10-28T00:01:00+00:00", "attempts": [],
    }
    for kind in ("feature_register", "feature_retrieve", "feature_retire"):
        assert JobOut.model_validate({**payload, "kind": kind}).kind == kind
        page = JobPageOut.model_validate({"items": [{
            **payload, "kind": kind, "attempt_count": 0, "latest_attempt": None,
        }], "next_cursor": None})
        assert page.items[0].kind == kind


def test_registration_list_restores_server_blocking_without_local_retirement():
    """폐기 완료와 조회 차단을 구별하고 첫 페이지·이전 페이지·새 세션에 전달한다."""
    states = ["queued", "running", "retired", "failed_attempt", "failed_before_attempt", "cancelled", "older"]
    ids = {state: str(UUID(int=index + 1)) for index, state in enumerate(states)}
    blocked = {ids[state] for state in ("queued", "running", "retired", "failed_attempt", "older")}
    visible = list(ids.values())[:6]
    checked_pages = []

    class RegistrationStore:
        def jobs(self, *, limit, before, actor_id, kind):
            assert limit == 7
            assert actor_id == "owner"
            assert kind == "feature_register"
            if before:
                assert before["id"] == ids["cancelled"]
            return [{
                "id": registration_id, "kind": "feature_register", "dataset_id": "source",
                "spec": {}, "status": "Succeeded", "cancel_requested": False,
                "result": {"feature_service": "service"}, "error": None,
                "created_at": "2026-10-28T00:00:00Z", "updated_at": "2026-10-28T00:00:00Z",
                "attempt_count": 1, "latest_attempt": None,
            } for registration_id in ([ids["older"]] if before else ids.values())]

        def retired_feature_registration_ids(self, job_ids):
            return {ids["retired"]} & set(job_ids)

        def blocked_feature_registration_ids(self, job_ids, *, include_failed=True):
            assert include_failed is True
            assert job_ids in (visible, [ids["older"]])
            checked_pages.append(job_ids)
            return blocked & set(job_ids)

    app = FastAPI()
    app.include_router(feast_router(RegistrationStore(), existing_dataset=None,
                                   require_submission_key=None))
    app.dependency_overrides[READ_JOB.dependency] = lambda: Actor(
        "owner", "human", frozenset({"viewer"})
    )
    for _ in range(2):
        with TestClient(app) as client:
            response = client.get("/api/features/services/registrations?limit=6")
            assert response.status_code == 200
            page = response.json()
            assert len(page["items"]) == 6
            for item in page["items"]:
                assert item["blocked"] == (item["id"] in blocked)
                assert item["retired"] == (item["id"] == ids["retired"])
            older = client.get("/api/features/services/registrations", params={
                "limit": 6, "cursor": page["next_cursor"],
            })
            assert older.status_code == 200
            item = older.json()["items"][0]
            assert item["id"] == ids["older"]
            assert item["blocked"] is True
            assert item["retired"] is False
    assert checked_pages == [visible, [ids["older"]], visible, [ids["older"]]]


def test_worker_checks_pinned_source_owner():
    from sos_platform.feast_features import verify_pinned_owner

    source = {"owner_actor": "researcher"}
    verify_pinned_owner(source, {"feature_source_owner": "researcher"}, "feature_source_owner")
    with pytest.raises(ValueError):
        verify_pinned_owner(source, {"feature_source_owner": "other"}, "feature_source_owner")
