"""등록 Feature 입력·권한·Job 응답의 중요한 계약만 빠르게 확인한다."""

import pytest
from pydantic import ValidationError

from sos_platform.feast_features import (
    FeatureDefinitionRequest,
    FeatureRetrievalRequest,
    feature_identity,
)
from sos_platform.schemas import JobOut


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


def test_worker_checks_pinned_source_owner():
    from sos_platform.feast_features import verify_pinned_owner

    source = {"owner_actor": "researcher"}
    verify_pinned_owner(source, {"feature_source_owner": "researcher"}, "feature_source_owner")
    with pytest.raises(ValueError):
        verify_pinned_owner(source, {"feature_source_owner": "other"}, "feature_source_owner")
