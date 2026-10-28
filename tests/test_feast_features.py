"""Feast 경계의 입력 계약과 고정 정의 식별자를 검증한다."""

from pydantic import ValidationError
import pytest

from sos_platform.feast_features import FeatureRetrievalRequest, feature_identity


def sample(**overrides):
    value = {
        "feature_dataset_id": "source",
        "entity_dataset_id": "events",
        "entity_column": "host_id",
        "timestamp_column": "event_time",
        "features": ["count_1h", "rate"],
    }
    value.update(overrides)
    return FeatureRetrievalRequest(**value)


@pytest.mark.parametrize("overrides", [
    {"entity_column": "event_time"},
    {"features": ["count_1h", "count_1h"]},
    {"features": ["sos_observation_id"]},
    {"features": ["host_id"]},
    {"features": ["event_time"]},
    {"ttl_seconds": 0},
])
def test_invalid_feature_contract(overrides):
    with pytest.raises(ValidationError):
        sample(**overrides)


def test_stable_feature_identity_uses_source_revision_and_feature_definition():
    source = {"id": "source", "version": "one"}
    baseline = feature_identity(sample(), source)
    assert baseline == feature_identity(sample(), dict(source))
    assert baseline != feature_identity(sample(), {"id": "source", "version": "two"})
    assert baseline != feature_identity(sample(ttl_seconds=100), source)
    assert baseline != feature_identity(sample(features=["rate", "count_1h"]), source)
    assert all(value.startswith("sos_") for value in baseline)
