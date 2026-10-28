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
