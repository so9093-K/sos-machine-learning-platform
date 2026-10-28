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
