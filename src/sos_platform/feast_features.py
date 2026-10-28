"""Feast가 등록 Feature 정의와 Point-in-Time 조회의 정본인 전문 Worker 모듈.

SOS는 Dataset 판본·Actor 검증·Job/Attempt·불변 결과의 정본이다.
Feast Registry는 재사용 가능한 Feature Service를 등록할 때만 갱신하고,
조회 Job은 기존 등록 정의를 재사용하며 Registry를 변경하지 않는다.
"""

import hashlib
import json
import os
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

import polars as pl
from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .datasets import pinned_scan_dataset


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


def _feature_schema(source: pl.LazyFrame, spec: FeatureDefinitionRequest):
    schema = source.collect_schema()
    required = [spec.entity_column, spec.timestamp_column, *spec.features]
    if any(name not in schema for name in required):
        raise ValueError("Source requires Entity, UTC Event Timestamp and declared Features")
    if schema[spec.entity_column] not in (pl.String, pl.Int32, pl.Int64):
        raise ValueError("Entity key must be a stable string or integer")
    _utc_timestamp(schema, spec.timestamp_column)
    numeric = (pl.Float32, pl.Float64, pl.Int32, pl.Int64)
    if any(schema[f] not in numeric for f in spec.features):
        raise ValueError("Only Float32/64 or Int32/64 Feature columns are supported")
    return schema


def verify_pinned_owner(dataset: dict, spec: dict, owner_field: str) -> None:
    """신뢰된 API가 제출 당시 검사한 소유자 ID가 Worker 실행 때도 유지되어야 한다.

    관리자에게 다른 Actor의 Dataset 접근을 허용하는 SOS 표준 권한을 유지하면서
    Dataset 소유자가 바뀐 경우에는 Job을 조용히 계속하지 않는다.
    """
    if owner_field not in spec or dataset.get("owner_actor") != spec[owner_field]:
        raise ValueError("Feature Dataset ownership evidence changed")


def _utc_timestamp(schema, column: str):
    if column not in schema:
        raise ValueError("Timestamp column is missing")
    dtype = schema[column]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
        raise ValueError("Event timestamps must be UTC Datetime")


def _feature_store():
    """Feast 전문 Runtime에서만 SDK를 불러오고 SQL Registry에 연결한다."""
    from feast import FeatureStore, RepoConfig

    registry_url = os.environ.get("SOS_FEAST_REGISTRY_URL")
    if not registry_url:
        raise RuntimeError("Feast SQL Registry is not configured")
    return FeatureStore(
        config=RepoConfig(
            project="sos_features",
            registry={"registry_type": "sql", "path": registry_url, "cache_ttl_seconds": 0},
            provider="local",
            offline_store={"type": "duckdb"},
            online_store=None,
        )
    )



def _semantic_spec(obj) -> dict:
    """Feast 원본 Proto의 의미적 정의만 비교한다. 생성·갱신 시각은 제외한다."""
    from google.protobuf.json_format import MessageToDict

    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items() if k != "meta"}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value

    return normalize(MessageToDict(obj.to_proto(), preserving_proto_field_name=True)["spec"])


def definition_digest(objects: list) -> str:
    """Entity·View·Service·Source를 포괄하는 내용 기반 정의 지문."""
    body = [_semantic_spec(obj) for obj in objects]
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _registry_objects(feast, names: tuple[str, str, str]) -> list:
    """일부만 존재하는 등록도 읽는다. 연결·권한 오류는 missing으로 취급하지 않는다."""
    from feast.errors import FeastObjectNotFoundException

    results = []
    for method, name in zip(
        (feast.get_entity, feast.get_feature_view, feast.get_feature_service), names
    ):
        try:
            results.append(method(name))
        except FeastObjectNotFoundException:
            results.append(None)
    return results


def validate_registry_definition(feast, names: tuple[str, str, str], expected: list) -> str:
    """동일 이름의 내용 변경·부분 삭제를 발견하면 조회를 중단한다."""
    actual = _registry_objects(feast, names)
    if any(item is None for item in actual):
        raise ValueError("Feast 등록 정의가 없거나 부분적으로 삭제됐습니다.")
    expected_digest = definition_digest(expected)
    if definition_digest(actual) != expected_digest:
        raise ValueError("Feast Registry 정의가 등록된 Feature 계약과 달라졌습니다.")
    return expected_digest


def _make_feature_objects(spec: FeatureDefinitionRequest, source_dataset: dict, schema):
    """등록과 조회가 동일한 Feast 객체 정의를 생성하도록 한곳에 모은다."""
    from feast import Entity, FeatureService, FeatureView, Field, FileSource
    from feast.types import Float32, Float64, Int32, Int64
    from feast.value_type import ValueType

    name, view_name, service_name = feature_identity(spec, source_dataset)
    settings = Settings.from_env()
    os.environ.setdefault("AWS_ACCESS_KEY_ID", settings.s3_access_key)
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", settings.s3_secret_key)
    os.environ.setdefault("AWS_DEFAULT_REGION", settings.s3_region)
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    source = FileSource(
        name=f"{view_name}_source",
        path=source_dataset["location"],
        timestamp_field=spec.timestamp_column,
        s3_endpoint_override=settings.s3_endpoint,
    )
    value_types = {pl.String: ValueType.STRING, pl.Int32: ValueType.INT32, pl.Int64: ValueType.INT64}
    types = {pl.Float32: Float32, pl.Float64: Float64, pl.Int32: Int32, pl.Int64: Int64}
    entity = Entity(
        name=name, value_type=value_types[schema[spec.entity_column]],
        join_keys=[spec.entity_column],
    )
    view = FeatureView(
        name=view_name, entities=[entity],
        ttl=timedelta(seconds=spec.ttl_seconds),
        schema=[Field(name=f, dtype=types[schema[f]]) for f in spec.features],
        source=source, online=False,
    )
    service = FeatureService(name=service_name, features=[view])
    return (name, view_name, service_name), [entity, view, service]


def register_feature_service(store, source_dataset: dict, raw_spec: dict) -> dict:
    """검증된 불변 Source의 Feature View/Service를 Feast Registry에 등록한다."""
    spec = FeatureDefinitionRequest.model_validate(raw_spec)
    if source_dataset["id"] != spec.feature_dataset_id:
        raise ValueError("Source Dataset identity changed")
    if source_dataset["version"] != raw_spec["feature_dataset_version"]:
        raise ValueError("Source Dataset revision changed")
    if source_dataset["kind"] != "file" or not source_dataset["location"].endswith(".parquet"):
        raise ValueError("Feature Source must be an immutable published Parquet Dataset")
    with pinned_scan_dataset(store, source_dataset) as source:
        schema = _feature_schema(source, spec)

    names, expected = _make_feature_objects(spec, source_dataset, schema)
    expected_digest = definition_digest(expected)
    feast = _feature_store()

    # 동일 이름의 concurrent apply 및 cleanup을 동일 SOS DB 자문 잠금으로 직렬화한다.
    # 외부 Feast 커밋이 완료되고 SOS Job 종료 전 중단되더라도, 다음 Attempt는
    # Registry를 읽어 동일 정의를 재사용한다. 전역 Registry 재등록은 하지 않는다.
    context = store.connection() if store is not None else nullcontext(None)
    with context as db:
        if db is not None:
            from sqlalchemy import text
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
                {"name": "sos-feast:" + names[2]},
            )
        current = _registry_objects(feast, names)
        for expected_item, existing in zip(expected, current):
            if existing is not None and _semantic_spec(existing) != _semantic_spec(expected_item):
                raise ValueError("같은 이름의 Feast 정의가 다른 내용으로 존재하여 덮어쓸 수 없습니다.")
        if not all(item is not None for item in current):
            feast.apply(expected)
        actual_digest = validate_registry_definition(_feature_store(), names, expected)
        if actual_digest != expected_digest:
            raise ValueError("Feast 등록 내용의 무결성 검사가 실패했습니다.")

    return {
        "feature_service": service_name,
        "feature_view": view_name,
        "feature_dataset_id": source_dataset["id"],
        "feature_dataset_version": source_dataset["version"],
        "entity_column": spec.entity_column,
        "timestamp_column": spec.timestamp_column,
        "feature_names": spec.features,
        "ttl_seconds": spec.ttl_seconds,
        "source_dtypes": [str(schema[f]) for f in spec.features],
        "source_location": source_dataset["location"],
        "definition_digest": expected_digest,
    }


def retrieve_features(
    store,
    feature_dataset: dict,
    entity_dataset: dict,
    registered: dict,
    raw_spec: dict,
    pending: Path,
) -> dict:
    """등록 서비스의 PIT Join 결과를 부모 Worker가 게시할 Parquet로 출력한다."""
    import pandas as pd

    request = FeatureRetrievalRequest.model_validate(raw_spec)
    if feature_dataset["id"] != registered["feature_dataset_id"]:
        raise ValueError("Registered Feature Source identity changed")
    if feature_dataset["version"] != registered["feature_dataset_version"]:
        raise ValueError("Registered Feature Source revision changed")
    if entity_dataset["id"] != request.entity_dataset_id:
        raise ValueError("Entity Dataset identity changed")
    if entity_dataset["version"] != raw_spec["entity_dataset_version"]:
        raise ValueError("Entity Dataset revision changed")

    key = registered["entity_column"]
    with pinned_scan_dataset(store, entity_dataset) as source:
        schema = source.collect_schema()
        if key not in schema:
            raise ValueError("Entity key column is missing")
        if schema[key] not in (pl.String, pl.Int32, pl.Int64):
            raise ValueError("Entity key must be string or integer")
        _utc_timestamp(schema, request.timestamp_column)
        # Type mismatch must be rejected before any Feast query.
        with pinned_scan_dataset(store, feature_dataset) as feature_source:
            source_schema = feature_source.collect_schema()
        if schema[key] != source_schema[key]:
            raise ValueError("Entity key dtype differs from registered Feature Source")
        # DuckDB .to_df() is materialized in memory: fail instead of sampling silently.
        limit = 100_000
        selected_columns = [key, request.timestamp_column]
        if "sos_observation_id" in schema:
            selected_columns.append("sos_observation_id")
        rows = source.select(selected_columns).head(limit + 1).collect()
        if rows.height == 0:
            raise ValueError("Entity Dataset is empty")
        if rows.height > limit:
            raise ValueError("Historical Feature Retrieval exceeds 100,000 Entity rows")
        entity_rows = rows.to_pandas().rename(
            columns={request.timestamp_column: "event_timestamp"}
        )
        entity_rows["event_timestamp"] = pd.to_datetime(entity_rows["event_timestamp"], utc=True)

    # 조회 중에는 apply()를 호출하지 않는다. Registry가 없거나 정의가 달라졌다면
    # 과거 결과를 새 정의로 조용히 재생성하지 않고 명확히 실패한다.
    if feature_dataset["location"] != registered.get("source_location"):
        raise ValueError("등록 당시의 Feature Source 위치가 변경됐습니다.")
    if not registered.get("definition_digest"):
        raise ValueError("정의 지문이 없는 등록 기록은 검증 없이 사용할 수 없습니다.")
    definition = FeatureDefinitionRequest(
        feature_dataset_id=feature_dataset["id"],
        entity_column=key,
        timestamp_column=registered["timestamp_column"],
        features=registered["feature_names"],
        ttl_seconds=registered["ttl_seconds"],
    )
    # Source의 실제 스키마를 근거로 같은 Feature 정의를 구성해 변경을 검증한다.
    if any(name not in source_schema for name in (key, definition.timestamp_column, *definition.features)):
        raise ValueError("등록 Feature Source 스키마를 확인할 수 없습니다.")
    names, expected = _make_feature_objects(definition, feature_dataset, source_schema)
    if names[2] != registered["feature_service"] or names[1] != registered["feature_view"]:
        raise ValueError("등록 이름과 Feature Service 정의가 일치하지 않습니다.")
    if definition_digest(expected) != registered["definition_digest"]:
        raise ValueError("Feature Source 스키마·정의 지문이 등록 시점과 다릅니다.")
    feast = _feature_store()
    validate_registry_definition(feast, names, expected)
    service = feast.get_feature_service(registered["feature_service"])
    # Feast 0.67 DuckDB Offline Store는 Ibis의 기본 DuckDB Backend를 사용하며,
    # FileSource.s3_endpoint_override만으로 그 Backend에 S3 URL/인증 설정이 전달되지 않는다.
    # 각 격리된 Worker 자식의 Backend를 SOS S3 호환 엔드포인트로 명시 구성한다.
    import ibis
    from urllib.parse import urlsplit

    settings = Settings.from_env()
    endpoint = urlsplit(settings.s3_endpoint)
    if (
        endpoint.scheme not in {"http", "https"} or not endpoint.hostname
        or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment
        or endpoint.username or endpoint.password
    ):
        raise ValueError("Feast S3 endpoint must be a plain HTTP(S) host")
    prior_backend = ibis.options.default_backend
    try:
        ibis.options.default_backend = ibis.duckdb.connect(
            s3_endpoint=endpoint.netloc,
            s3_use_ssl=(endpoint.scheme == "https"),
            s3_url_style="path",
            s3_region=settings.s3_region,
            s3_access_key_id=settings.s3_access_key,
            s3_secret_access_key=settings.s3_secret_key,
        )
        output = feast.get_historical_features(
            entity_df=entity_rows,
            features=service,
        ).to_df()
    finally:
        ibis.options.default_backend = prior_backend
    required = [key, "event_timestamp", *registered["feature_names"]]
    if "sos_observation_id" in entity_rows.columns:
        required.append("sos_observation_id")
    if any(name not in output.columns for name in required):
        raise ValueError("Feast returned a Feature schema different from registration")
    if len(output) != len(entity_rows):
        raise ValueError("Feast returned a different Entity row count")
    output = output[required].rename(columns={"event_timestamp": request.timestamp_column})
    output.to_parquet(pending, index=False)

    return {
        "registration_job_id": request.registration_job_id,
        "feature_service": registered["feature_service"],
        "feature_view": registered["feature_view"],
        "feature_dataset_id": feature_dataset["id"],
        "feature_dataset_version": feature_dataset["version"],
        "entity_dataset_id": entity_dataset["id"],
        "entity_dataset_version": entity_dataset["version"],
        "entity_column": key,
        "timestamp_column": request.timestamp_column,
        "source_timestamp_column": registered["timestamp_column"],
        "feature_names": registered["feature_names"],
        "ttl_seconds": registered["ttl_seconds"],
        "row_count": len(output),
        "definition_digest": registered["definition_digest"],
        "engine": "feast",
    }
