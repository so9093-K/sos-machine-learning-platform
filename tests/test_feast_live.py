"""Feast 실 SDK로 PIT Join과 SOS 불변 결과 경계를 확인한다.

이 검사는 전문 Runtime의 Feast 설치가 있는 환경에서만 실행한다.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import polars as pl
import pytest

pytest.importorskip("feast")

from sos_platform import feast_features


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 1, hour, minute, tzinfo=timezone.utc)


def test_historical_feature_values_do_not_see_future_events(tmp_path, monkeypatch):
    source = tmp_path / "feature_source.parquet"
    events = tmp_path / "prediction_events.parquet"
    output = tmp_path / "result.parquet"
    pl.DataFrame(
        {
            "host_id": ["a", "a", "a"],
            "event_timestamp": [utc(9), utc(10), utc(11)],
            "file_changes_1h": [10.0, 20.0, 30.0],
        }
    ).write_parquet(source)
    pl.DataFrame(
        {
            "host_id": ["a", "a", "a", "a"],
            "event_timestamp": [utc(8, 30), utc(9, 30), utc(10, 30), utc(11, 30)],
        }
    ).write_parquet(events)
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
    monkeypatch.setattr(
        feast_features.Settings,
        "from_env",
        lambda: SimpleNamespace(
            s3_access_key="testing", s3_secret_key="testing", s3_region="us-east-1",
            s3_endpoint="http://127.0.0.1:9000"
        ),
    )
    monkeypatch.setenv("SOS_FEAST_REGISTRY_URL", f"sqlite:///{tmp_path / 'registry.db'}")

    result = feast_features.retrieve_features(
        None,
        source_dataset,
        entity_dataset,
        {
            "feature_dataset_id": "source",
            "feature_dataset_version": "v1",
            "entity_dataset_id": "events",
            "entity_dataset_version": "v2",
            "entity_column": "host_id",
            "timestamp_column": "event_timestamp",
            "features": ["file_changes_1h"],
            "ttl_seconds": 86400,
        },
        output,
    )
    assert result["feature_service"].startswith("sos_fs_")
    assert result["row_count"] == 4
    assert output.exists()
    records = pl.read_parquet(output).sort("event_timestamp")
    assert records["file_changes_1h"].to_list()[1:] == [10.0, 20.0, 30.0]
    assert records["file_changes_1h"].is_null().to_list()[0]
