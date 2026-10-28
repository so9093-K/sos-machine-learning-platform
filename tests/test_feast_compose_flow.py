"""실제 Compose API·Worker·SeaweedFS·Registry·학습 계보 집중 검증.

독립 검증 작업에서만 SOS_TEST_FEAST_COMPOSE=1로 실행한다.
일반 pytest에서는 무거운 Compose 실행을 강제하지 않는다.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
import time
from uuid import uuid4

import httpx
import polars as pl
import pytest


@pytest.mark.skipif(
    os.getenv("SOS_TEST_FEAST_COMPOSE") != "1",
    reason="requires running full SOS API/Worker storage services",
)
def test_registered_feature_to_model_revision():
    client = httpx.Client(
        base_url="http://127.0.0.1:9602",
        headers={"Authorization": "Bearer review-feast-full-compose-token-value-012345678901234"},
        timeout=15.0,
    )

    def upload(name: str, df: pl.DataFrame) -> dict:
        handle = BytesIO()
        df.write_parquet(handle)
        response = client.post(
            "/api/datasets/files", files={"file": (name, handle.getvalue(), "application/octet-stream")}
        )
        assert response.status_code == 201, response.text[:1000]
        return response.json()

    def submit(path: str, payload: dict) -> dict:
        response = client.post(
            path, json=payload, headers={"Idempotency-Key": f"review-{uuid4().hex}"}
        )
        assert response.status_code == 202, response.text[:1000]
        return response.json()

    def completed(job: dict) -> dict:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = client.get(f"/api/jobs/{job['id']}")
            assert response.status_code == 200, response.text[:1000]
            result = response.json()
            if result["status"] == "Succeeded":
                return result
            if result["status"] in ("Failed", "Cancelled"):
                pytest.fail(f"{result['kind']} job ended: {result['status']} {result.get('error_code')} {result.get('error')}")
            time.sleep(1)
        pytest.fail(f"{job['kind']} job did not finish within the diagnostic window")

    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    observed = list(range(40))
    source = pl.DataFrame({
        "host_id": ["host-a"] * len(observed),
        "event_timestamp": [t0 + timedelta(minutes=10 * i) for i in observed],
        "activity_rate": [float(i + 10) for i in observed],
    })
    events = pl.DataFrame({
        "host_id": ["host-a"] * len(observed),
        "prediction_time": [t0 + timedelta(minutes=10 * i + 5) for i in observed],
        "sos_observation_id": [str(uuid4()) for _ in observed],
    })
    feature_source = upload("review-feature-source.parquet", source)
    entity_source = upload("review-prediction-events.parquet", events)
    registration = completed(submit("/api/features/services/jobs", {
        "feature_dataset_id": feature_source["id"],
        "entity_column": "host_id",
        "timestamp_column": "event_timestamp",
        "features": ["activity_rate"],
    }))
    assert registration["result"]["feature_service"]
    list_response = client.get("/api/features/services/registrations")
    assert list_response.status_code == 200, list_response.text[:1000]
    assert any(item["id"] == registration["id"] for item in list_response.json())

    retrieval = completed(submit("/api/features/retrieval/jobs", {
        "registration_job_id": registration["id"],
        "entity_dataset_id": entity_source["id"],
        "timestamp_column": "prediction_time",
    }))
    retrieved_dataset_id = retrieval["result"]["dataset_id"]
    assert retrieved_dataset_id
    ds_response = client.get(f"/api/datasets/{retrieved_dataset_id}")
    assert ds_response.status_code == 200, ds_response.text[:1000]
    from sqlalchemy import create_engine, text
    from sos_platform.config import Settings
    with create_engine(Settings.from_env().database_url).connect() as connection:
        options = connection.execute(
            text("SELECT source_job_id FROM datasets WHERE id=:id"),
            {"id": retrieved_dataset_id},
        ).scalar_one()
    assert options == retrieval["id"]

    training = completed(submit("/api/ml/train/jobs", {
        "dataset_id": retrieved_dataset_id,
        "features": ["activity_rate"],
        "experiment_title": "Feast E2E Review",
    }))
    model_revision_id = training["result"]["model_revision_id"]
    model_resp = client.get(f"/api/ml/models/{model_revision_id}")
    assert model_resp.status_code == 200, model_resp.text[:1000]
    origin = model_resp.json()["runtime"]["feature_retrieval"]
    assert origin["registration_job_id"] == registration["id"]
    assert origin["retrieval_job_id"] == retrieval["id"]
    assert origin["feature_service"] == registration["result"]["feature_service"]
