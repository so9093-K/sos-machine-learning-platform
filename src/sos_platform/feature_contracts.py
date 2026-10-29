"""SOS Feature 등록·조회 입력 계약과 불변 정의 식별. SDK·Worker를 참조하지 않는다."""

import hashlib
import json

from pydantic import BaseModel, Field, model_validator


class FeatureDefinitionRequest(BaseModel):
    """안정적인 Source 판본에 고정되는 Feast Feature Service 정의."""

    feature_dataset_id: str = Field(min_length=1)
    entity_column: str = Field(min_length=1, max_length=256)
    timestamp_column: str = Field(min_length=1, max_length=256)
    features: list[str] = Field(min_length=1, max_length=128)
    ttl_seconds: int = Field(default=2_592_000, ge=1, le=315_360_000)

    @model_validator(mode="after")
    def validate_definition(self):
        if self.entity_column == self.timestamp_column:
            raise ValueError("Entity and event time columns must be different")
        if len(self.features) != len(set(self.features)):
            raise ValueError("Feature columns must be unique")
        if set(self.features) & {self.entity_column, self.timestamp_column, "sos_observation_id"}:
            raise ValueError("Entity, time, and observation identity cannot be features")
        return self


class FeatureRetrievalRequest(BaseModel):
    """기존 등록 Feature Service를 재사용하는 Entity 시점 조회 요청."""

    registration_job_id: str = Field(min_length=1)
    entity_dataset_id: str = Field(min_length=1)
    timestamp_column: str = Field(min_length=1, max_length=256)


def feature_identity(spec: FeatureDefinitionRequest, dataset: dict) -> tuple[str, str, str]:
    """등록된 Source Dataset 판본·스키마·TTL 변경 시 새 정의를 발급한다."""
    payload = {
        "dataset_id": dataset["id"],
        "version": dataset["version"],
        "entity_column": spec.entity_column,
        "timestamp_column": spec.timestamp_column,
        "features": spec.features,
        "ttl_seconds": spec.ttl_seconds,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sos_entity_{digest[:24]}", f"sos_fv_{digest[:24]}", f"sos_fs_{digest[:24]}"
