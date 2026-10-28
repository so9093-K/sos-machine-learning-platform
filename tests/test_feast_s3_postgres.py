"""Feast SQL Registry + SeaweedFS S3 실제 연동을 위한 단일 집중 통합 검사.

일반 제품 검증에서는 skip. 필요한 두 외부 서비스가 있는 Runtime 검토에서만 실행.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os

import boto3
import polars as pl
import pytest
from botocore.config import Config

pytest.importorskip("feast")

from sos_platform import feast_features
from sos_platform.config import Settings


@pytest.mark.skipif(
    os.getenv("SOS_TEST_FEAST_STORAGE_INTEGRATION") != "1",
    reason="requires real S3 and SQL registry services",
)
def test_feast_seaweed_s3_and_postgres_registry(tmp_path, monkeypatch):
    settings = Settings.from_env()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except Exception:
        client.create_bucket(Bucket=settings.s3_bucket)

    def utc(h, m=0):
        return datetime(2026, 10, 28, h, m, tzinfo=timezone.utc)

    local_source = tmp_path / "feature-source.parquet"
    events = tmp_path / "events.parquet"
    output = tmp_path / "joined.parquet"

    pl.DataFrame({
        "host_id": ["a", "a"],
        "event_timestamp": [utc(9), utc(10)],
        "file_changes_1h": [7.0, 11.0],
    }).write_parquet(local_source)
    pl.DataFrame({
        "host_id": ["a", "a"],
        "prediction_time": [utc(9, 30), utc(10, 30)],
        "sos_observation_id": [
            "00000000-0000-4000-8000-000000000011",
            "00000000-0000-4000-8000-000000000012",
        ],
    }).write_parquet(events)

    key = "datasets/review-feature-source.parquet"
    client.upload_file(str(local_source), settings.s3_bucket, key)
    source_dataset = {
        "id": "s3-source", "kind": "file",
        "location": f"s3://{settings.s3_bucket}/{key}", "version": "test-version",
    }
    entity_dataset = {
        "id": "entity", "kind": "file",
        "location": str(events), "version": "events-version",
    }

    @contextmanager
    def pinned(_store, dataset):
        local = local_source if dataset["id"] == "s3-source" else events
        yield pl.scan_parquet(local)
    monkeypatch.setattr(feast_features, "pinned_scan_dataset", pinned)

    registration = feast_features.register_feature_service(None, source_dataset, {
        "feature_dataset_id": "s3-source",
        "feature_dataset_version": "test-version",
        "entity_column": "host_id",
        "timestamp_column": "event_timestamp",
        "features": ["file_changes_1h"],
        "ttl_seconds": 86400,
    })
    assert registration["feature_service"]
    result = feast_features.retrieve_features(
        None, source_dataset, entity_dataset, registration,
        {
            "registration_job_id": "registration-job",
            "entity_dataset_id": "entity",
            "entity_dataset_version": "events-version",
            "timestamp_column": "prediction_time",
        }, output,
    )
    assert result["row_count"] == 2
    observed = pl.read_parquet(output).sort("prediction_time")
    assert observed["file_changes_1h"].to_list() == [7.0, 11.0]
    assert observed["sos_observation_id"].to_list() == [
        "00000000-0000-4000-8000-000000000011",
        "00000000-0000-4000-8000-000000000012",
    ]
