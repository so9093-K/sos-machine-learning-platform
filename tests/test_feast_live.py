"""실제 Feast SDK로 등록 Feature Service의 재사용과 PIT Join을 검증한다."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import polars as pl
import pytest

pytest.importorskip("feast")

from sos_platform import feast_features


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 1, hour, minute, tzinfo=timezone.utc)


def test_registered_service_reused_for_historical_point_in_time(tmp_path, monkeypatch):
    source = tmp_path / "feature_source.parquet"
    events = tmp_path / "prediction_events.parquet"
    output = tmp_path / "result.parquet"
    pl.DataFrame({
        "host_id": ["a", "a", "a"],
        "event_timestamp": [utc(9), utc(10), utc(11)],
        "file_changes_1h": [10.0, 20.0, 30.0],
    }).write_parquet(source)
    pl.DataFrame({
        "host_id": ["a", "a", "a", "a"],
        "event_timestamp": [utc(8, 30), utc(9, 30), utc(10, 30), utc(11, 30)],
        "sos_observation_id": [
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            "00000000-0000-4000-8000-000000000003",
            "00000000-0000-4000-8000-000000000004",
        ],
    }).write_parquet(events)
    source_dataset = {
        "id": "source", "kind": "file", "location": str(source), "version": "v1"
    }
    entity_dataset = {
        "id": "events", "kind": "file", "location": str(events), "version": "v2"
    }

    @contextmanager
    def pinned(_store, dataset):
        yield pl.scan_parquet(dataset["location"])

    monkeypatch.setattr(feast_features, "pinned_scan_dataset", pinned)
    monkeypatch.setattr(feast_features.Settings, "from_env", lambda: SimpleNamespace(
        s3_access_key="testing", s3_secret_key="testing", s3_region="us-east-1",
        s3_endpoint="http://127.0.0.1:9000",
    ))
    monkeypatch.setenv("SOS_FEAST_REGISTRY_URL", f"sqlite:///{tmp_path / 'registry.db'}")

    registered = feast_features.register_feature_service(None, source_dataset, {
        "feature_dataset_id": "source",
        "feature_dataset_version": "v1",
        "entity_column": "host_id",
        "timestamp_column": "event_timestamp",
        "features": ["file_changes_1h"],
        "ttl_seconds": 86400,
    })
    assert registered["feature_service"].startswith("sos_fs_")
    # 외부 커밋 이후 SOS 응답을 잃어도 동일 정의를 재실행하면 재등록 없이 수렴한다.
    replay = feast_features.register_feature_service(None, source_dataset, {
        "feature_dataset_id": "source",
        "feature_dataset_version": "v1",
        "entity_column": "host_id",
        "timestamp_column": "event_timestamp",
        "features": ["file_changes_1h"],
        "ttl_seconds": 86400,
    })
    assert replay["definition_digest"] == registered["definition_digest"]
    result = feast_features.retrieve_features(
        None, source_dataset, entity_dataset, registered,
        {
            "registration_job_id": "registration",
            "entity_dataset_id": "events",
            "entity_dataset_version": "v2",
            "timestamp_column": "event_timestamp",
        },
        output,
    )
    assert result["feature_service"] == registered["feature_service"]
    assert result["row_count"] == 4
    records = pl.read_parquet(output).sort("event_timestamp")
    assert records["file_changes_1h"].to_list()[1:] == [10.0, 20.0, 30.0]
    assert records["file_changes_1h"].is_null().to_list()[0]
    assert records["sos_observation_id"].to_list() == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
        "00000000-0000-4000-8000-000000000004",
    ]


def test_registry_conflict_and_partial_recovery(tmp_path, monkeypatch):
    """정의 일부가 제거되면 안전하게 재조정하고 변경된 내용은 덮어쓰지 않는다."""
    source = tmp_path / "source.parquet"
    pl.DataFrame({
        "host_id": ["a"], "event_timestamp": [utc(9)], "number": [3.0],
    }).write_parquet(source)
    source_dataset = {"id": "source", "kind": "file", "location": str(source), "version": "v1"}

    @contextmanager
    def pinned(_store, dataset):
        yield pl.scan_parquet(dataset["location"])
    monkeypatch.setattr(feast_features, "pinned_scan_dataset", pinned)
    monkeypatch.setattr(feast_features.Settings, "from_env", lambda: SimpleNamespace(
        s3_access_key="testing", s3_secret_key="testing", s3_region="us-east-1",
        s3_endpoint="http://127.0.0.1:9000",
    ))
    monkeypatch.setenv("SOS_FEAST_REGISTRY_URL", f"sqlite:///{tmp_path / 'registry.db'}")
    spec = {"feature_dataset_id": "source", "feature_dataset_version": "v1",
            "entity_column": "host_id", "timestamp_column": "event_timestamp",
            "features": ["number"], "ttl_seconds": 86400}
    registered = feast_features.register_feature_service(None, source_dataset, spec)
    registry = feast_features._feature_store()
    registry.delete_feature_service(registered["feature_service"])
    recovered = feast_features.register_feature_service(None, source_dataset, spec)
    assert recovered["definition_digest"] == registered["definition_digest"]

    # 관리자가 Registry 정의를 임의 변경한 경우 조용히 덮어쓰면 안 된다.
    registry = feast_features._feature_store()
    view = registry.get_feature_view(registered["feature_view"])
    view.ttl = __import__("datetime").timedelta(seconds=3600)
    registry.apply(view)
    with pytest.raises(ValueError, match="다른 내용"):
        feast_features.register_feature_service(None, source_dataset, spec)
