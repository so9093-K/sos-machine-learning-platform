"""Feast 기반 등록 Feature의 역사적 조회. SOS의 불변 결과 게시가 정본이다.

Feast SDK는 Worker의 Feature 전용 Runtime에서만 import한다. 일반 API, Explorer,
일반 Data Preparation은 Feast Python 의존성에 결합되지 않는다.
"""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import polars as pl
from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .datasets import pinned_scan_dataset


class FeatureRetrievalRequest(BaseModel):
    """서로 다른 Feature Source와 조회 시점 Dataset을 명시한다."""

    feature_dataset_id: str = Field(min_length=1)
    entity_dataset_id: str = Field(min_length=1)
    entity_column: str = Field(min_length=1, max_length=256)
    timestamp_column: str = Field(min_length=1, max_length=256)
    features: list[str] = Field(min_length=1, max_length=128)
    ttl_seconds: int = Field(default=2_592_000, ge=1, le=315_360_000)

    @model_validator(mode="after")
    def unique_fields(self):
        if self.entity_column == self.timestamp_column:
            raise ValueError("Entity and time columns must be different")
        if len(self.features) != len(set(self.features)):
            raise ValueError("Feature columns must be unique")
        if set(self.features) & {self.entity_column, self.timestamp_column, "sos_observation_id"}:
            raise ValueError("Entity, time and observation identity cannot be features")
        return self


def _schema(source: pl.LazyFrame, spec: FeatureRetrievalRequest, *, feature_source: bool):
    schema = source.collect_schema()
    expected = [spec.entity_column, spec.timestamp_column]
    if feature_source:
        expected += spec.features
    if any(name not in schema for name in expected):
        raise ValueError("Required Entity, UTC time, or Feature column is missing")
    if schema[spec.entity_column] not in (pl.String, pl.Int32, pl.Int64):
        raise ValueError("Entity key must be a stable string or integer")
    event_type = schema[spec.timestamp_column]
    if not isinstance(event_type, pl.Datetime) or event_type.time_zone != "UTC":
        raise ValueError("Feature and Entity event timestamps must be UTC Datetime")
    if feature_source:
        numeric = (pl.Float32, pl.Float64, pl.Int32, pl.Int64)
        if any(schema[f] not in numeric for f in spec.features):
            raise ValueError("Only explicit numeric Feature columns are supported")
    return schema


def feature_identity(spec: FeatureRetrievalRequest, dataset: dict) -> tuple[str, str, str]:
    """원본 Dataset 판본과 Feature 계약을 바꾸면 새 Feast 정의를 사용한다."""
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


def retrieve_features(store, feature_dataset: dict, entity_dataset: dict, raw_spec: dict, pending: Path) -> dict:
    """Feast Point-in-Time Retrieval을 수행하고 결과 파일은 부모 Worker가 게시한다."""
    from feast import Entity, FeatureService, FeatureStore, FeatureView, Field, FileSource, RepoConfig
    from feast.types import Float32, Float64, Int32, Int64
    import pandas as pd

    spec = FeatureRetrievalRequest.model_validate(raw_spec)
    if feature_dataset["id"] != spec.feature_dataset_id or entity_dataset["id"] != spec.entity_dataset_id:
        raise ValueError("Feature or Entity Dataset reference changed")
    if feature_dataset["version"] != raw_spec["feature_dataset_version"]:
        raise ValueError("Feature Source Dataset revision changed")
    if entity_dataset["version"] != raw_spec["entity_dataset_version"]:
        raise ValueError("Entity Dataset revision changed")
    # FileSource의 S3 경로는 SOS가 게시한 Parquet 원본에 한정한다.
    if feature_dataset["kind"] != "file" or not feature_dataset["location"].endswith(".parquet"):
        raise ValueError("Feast Feature Source requires a published Parquet Dataset")

    with pinned_scan_dataset(store, feature_dataset) as source:
        source_schema = _schema(source, spec, feature_source=True)
    with pinned_scan_dataset(store, entity_dataset) as source:
        entity_schema = _schema(source, spec, feature_source=False)
        if entity_schema[spec.entity_column] != source_schema[spec.entity_column]:
            raise ValueError("Entity join key types differ")
        if source.select(pl.len()).collect().item() == 0:
            raise ValueError("Entity Dataset is empty")
        # DuckDB의 RetrievalJob.to_df()는 결과를 메모리에 물질화한다.
        # 조용히 일부만 선택하지 않고 초과 요청을 정확히 거부한다.
        limit = 100_000
        df = source.select([spec.entity_column, spec.timestamp_column]).head(limit + 1).collect()
        if df.height > limit:
            raise ValueError("Feature retrieval exceeds the current 100,000-row execution limit")
        entity_rows = df.to_pandas().rename(columns={spec.timestamp_column: "event_timestamp"})
        entity_rows["event_timestamp"] = pd.to_datetime(entity_rows["event_timestamp"], utc=True)

    registry_url = os.environ.get("SOS_FEAST_REGISTRY_URL")
    if not registry_url:
        raise RuntimeError("Feast SQL Registry is not configured")
    config = RepoConfig(
        project="sos_features",
        registry={"registry_type": "sql", "path": registry_url, "cache_ttl_seconds": 0},
        provider="local",
        offline_store={"type": "duckdb"},
        online_store="null",
    )
    feast = FeatureStore(config=config)
    name, view_name, service_name = feature_identity(spec, feature_dataset)
    data_types = {pl.Float32: Float32, pl.Float64: Float64, pl.Int32: Int32, pl.Int64: Int64}
    settings = Settings.from_env()
    os.environ.setdefault("AWS_ACCESS_KEY_ID", settings.s3_access_key)
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", settings.s3_secret_key)
    os.environ.setdefault("AWS_DEFAULT_REGION", settings.s3_region)
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    source = FileSource(
        name=f"{view_name}_source",
        path=feature_dataset["location"],
        timestamp_field=spec.timestamp_column,
        s3_endpoint_override=settings.s3_endpoint,
    )
    entity = Entity(name=name, join_keys=[spec.entity_column])
    view = FeatureView(
        name=view_name,
        entities=[entity],
        ttl=timedelta(seconds=spec.ttl_seconds),
        schema=[Field(name=f, dtype=data_types[source_schema[f]]) for f in spec.features],
        source=source,
        online=False,
    )
    service = FeatureService(name=service_name, features=[view])
    feast.apply([entity, view, service])
    output = feast.get_historical_features(
        entity_df=entity_rows, features=feast.get_feature_service(service_name)
    ).to_df()
    required = [spec.entity_column, "event_timestamp", *spec.features]
    if any(column not in output.columns for column in required):
        raise ValueError("Feast returned an invalid Feature schema")
    if len(output) != len(entity_rows):
        raise ValueError("Feast returned a different number of Entity rows")
    output = output[required].rename(columns={"event_timestamp": spec.timestamp_column})
    output.to_parquet(pending, index=False)
    return {
        "source_dataset_id": feature_dataset["id"],
        "source_dataset_version": feature_dataset["version"],
        "entity_dataset_id": entity_dataset["id"],
        "entity_dataset_version": entity_dataset["version"],
        "feature_view": view_name,
        "feature_service": service_name,
        "feature_names": spec.features,
        "entity_column": spec.entity_column,
        "timestamp_column": spec.timestamp_column,
        "ttl_seconds": spec.ttl_seconds,
        "row_count": len(output),
        "engine": "feast",
    }
