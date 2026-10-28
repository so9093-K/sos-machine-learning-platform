"""Feast 등록·조회 입력 계약과 정본 정의 식별자를 검증한다."""

import pytest
from pydantic import ValidationError

from sos_platform.feast_features import (
    FeatureDefinitionRequest,
    FeatureRetrievalRequest,
    feature_identity,
)


def sample(**overrides):
    value = {
        "feature_dataset_id": "source",
        "entity_column": "host_id",
        "timestamp_column": "event_time",
        "features": ["count_1h", "rate"],
    }
    value.update(overrides)
    return FeatureDefinitionRequest(**value)


@pytest.mark.parametrize("overrides", [
    {"entity_column": "event_time"},
    {"features": ["count_1h", "count_1h"]},
    {"features": ["sos_observation_id"]},
    {"features": ["host_id"]},
    {"features": ["event_time"]},
    {"ttl_seconds": 0},
])
def test_invalid_feature_definition(overrides):
    with pytest.raises(ValidationError):
        sample(**overrides)


def test_stable_definition_identity_depends_on_source_revision_and_semantics():
    source = {"id": "source", "version": "one"}
    baseline = feature_identity(sample(), source)
    assert baseline == feature_identity(sample(), dict(source))
    assert baseline != feature_identity(sample(), {"id": "source", "version": "two"})
    assert baseline != feature_identity(sample(ttl_seconds=100), source)
    assert baseline != feature_identity(sample(features=["rate", "count_1h"]), source)


def test_retrieval_requires_registered_job_reference():
    with pytest.raises(ValidationError):
        FeatureRetrievalRequest(entity_dataset_id="entity", timestamp_column="event_time")
    assert FeatureRetrievalRequest(
        registration_job_id="registered", entity_dataset_id="entity",
        timestamp_column="event_time"
    ).registration_job_id == "registered"


def test_feature_job_responses_match_the_real_store_shapes():
    from sos_platform.schemas import JobOut, JobSummaryOut

    full = {
        "id": "reg-job", "kind": "feature_register", "dataset_id": "source",
        "spec": {"feature_dataset_id": "source"}, "status": "Succeeded",
        "cancel_requested": False, "result": {"feature_service": "sos_fs_123"},
        "error": None, "created_at": "2026-10-28T00:00:00+00:00",
        "updated_at": "2026-10-28T00:01:00+00:00", "attempts": [],
    }
    assert JobOut.model_validate(full).kind == "feature_register"
    assert JobOut.model_validate({**full, "kind": "feature_retrieve"}).kind == "feature_retrieve"
    summary = {
        **{key: value for key, value in full.items() if key != "attempts"},
        "attempt_count": 1,
        "latest_attempt": None,
    }
    assert JobSummaryOut.model_validate(summary).kind == "feature_register"
    assert JobSummaryOut.model_validate({**summary, "kind": "feature_retrieve"}).kind == "feature_retrieve"


def test_feature_openapi_contract_uses_summary_for_registration_listing(tmp_path):
    from sos_platform.api import create_app
    from sos_platform.security import IdentitySettings

    app = create_app(tmp_path, identity=IdentitySettings("development", {}))
    schema = app.openapi()
    feature_paths = schema["paths"]
    assert feature_paths["/api/features/services/jobs"]["post"]["responses"]["202"]["content"][
        "application/json"]["schema"]["$ref"] == "#/components/schemas/JobOut"
    assert feature_paths["/api/features/services/registrations"]["get"]["responses"]["200"][
        "content"]["application/json"]["schema"]["items"]["$ref"] == "#/components/schemas/JobSummaryOut"
    assert feature_paths["/api/features/retrieval/jobs"]["post"]["responses"]["202"][
        "content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/JobOut"


def test_pinned_owner_allows_admin_authorized_foreign_source_but_rejects_changes():
    from sos_platform.feast_features import verify_pinned_owner

    source = {"id": "source", "owner_actor": "researcher-a"}
    spec = {"feature_source_owner": "researcher-a"}
    # API의 resource_allowed()가 관리자에게 허용한 다른 Actor Source도 Worker가 유지한다.
    verify_pinned_owner(source, spec, "feature_source_owner")
    with pytest.raises(ValueError):
        verify_pinned_owner({**source, "owner_actor": "researcher-b"}, spec, "feature_source_owner")
    with pytest.raises(ValueError):
        verify_pinned_owner(source, {}, "feature_source_owner")
