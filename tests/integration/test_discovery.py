"""DuckDB Discovery의 분석 의미·권한·Job·Iceberg Snapshot 계약을 검증한다."""

import math
from io import BytesIO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sos_platform.discovery import DiscoveryPolicy, quoted, source_connection
from sos_platform.worker import run_once


def _upload(client, name: str, content: bytes) -> dict:
    response = client.post(
        "/api/datasets/files", files={"file": (name, content, "application/octet-stream")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_discovery_overview_search_and_bounded_preview(workspace):
    client, _store = workspace
    dataset = _upload(client, "measurements.csv", b"group,value\na,1\nb,2\na,3\n")
    base = f"/api/datasets/{dataset['id']}"
    overview = client.get(base + "/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["row_count"] is None
    assert overview.json()["row_count_basis"] == "not_computed"
    assert overview.json()["column_count"] == 2
    assert overview.json()["size_bytes"] > 0
    assert overview.json()["version"] == dataset["version"]
    columns = client.get(base + "/columns?search=val")
    assert columns.status_code == 200, columns.text
    assert columns.json()["total"] == 2
    assert [item["name"] for item in columns.json()["columns"]] == ["value"]
    preview = client.get(base + "/preview?limit=2")
    assert preview.status_code == 200, preview.text
    assert preview.json()["returned_rows"] == 2
    assert preview.json()["schema"]["value"] == "Int64"


def test_full_grouped_exploration_and_profile_job(workspace):
    client, store = workspace
    dataset = _upload(client, "scores.csv", b"group,value\na,1\na,3\nb,5\nb,\n")
    base = f"/api/datasets/{dataset['id']}"
    result = client.post(
        base + "/explorations",
        json={"column": "value", "metric": "mean", "group_by": "group"},
    )
    assert result.status_code == 200, result.text
    info = result.json()
    assert info["scope"] == "sample"
    assert info["sample_limit"] >= 4
    assert info["version"] == dataset["version"]
    values = {row["category"]: row["value"] for row in info["rows"]}
    assert values == {"a": 2, "b": 5}
    full_job = client.post(
        "/api/explorations/jobs",
        json={
            "dataset_id": dataset["id"],
            "exploration": {"column": "value", "metric": "mean", "group_by": "group"},
        },
    )
    assert full_job.status_code == 202, full_job.text
    assert run_once(store)
    full = client.get(f"/api/explorations/jobs/{full_job.json()['id']}").json()
    assert full["status"] == "Succeeded", full
    assert full["result"]["scope"] == "full"
    assert full["result"]["sample_limit"] is None
    assert client.get(f"/api/jobs/{full_job.json()['id']}").status_code == 404
    assert all(job["kind"] != "exploration" for job in client.get("/api/jobs").json()["items"])
    assert any(
        job["id"] == full_job.json()["id"]
        for job in client.get("/api/explorations/jobs").json()["items"]
    )
    assert {r["category"]: r["value"] for r in full["result"]["rows"]} == values
    immediate = client.get(base + "/profile")
    assert immediate.status_code == 200, immediate.text
    assert immediate.json()["sampled"] is True
    assert immediate.json()["row_count"] == 4
    requested = client.post("/api/jobs", json={"kind": "profile", "dataset_id": dataset["id"]})
    assert requested.status_code == 202
    assert run_once(store)
    completed = client.get(f"/api/jobs/{requested.json()['id']}").json()
    assert completed["status"] == "Succeeded", completed
    assert completed["result"]["sampled"] is False
    assert completed["result"]["row_count"] == 4


def test_no_untrusted_identifiers_or_sql_are_executed(workspace):
    client, _store = workspace
    dataset = _upload(client, "quotes.csv", b'"group""quote",value\na,10\n')
    base = f"/api/datasets/{dataset['id']}"
    assert quoted('a"b') == '"a""b"'
    malicious = client.post(
        base + "/explorations",
        json={"column": "value); SELECT * FROM read_csv('/etc/passwd') --", "metric": "max"},
    )
    assert malicious.status_code == 422, malicious.text
    valid = client.post(
        base + "/explorations",
        json={"column": "value", "group_by": 'group"quote', "metric": "count"},
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["rows"][0]["value"] == 1


def test_discovery_does_not_read_other_local_files(workspace, tmp_path):
    client, store = workspace
    dataset = _upload(client, "source.csv", b"value\n123\n")
    other = tmp_path / "confidential.csv"
    other.write_text("private\nsecret\n")
    with source_connection(store, dataset) as conn:
        with pytest.raises(Exception, match="(?i)permission|access|external"):
            conn.execute(f"SELECT * FROM read_csv('{other}')").fetchall()
        assert conn.execute("SELECT count(*) FROM source").fetchone()[0] == 1


def test_discovery_policy_validation(monkeypatch):
    monkeypatch.setenv("SOS_DISCOVERY_MEMORY_MIB", "512")
    assert DiscoveryPolicy.from_env().memory_mib == 512
    monkeypatch.setenv("SOS_DISCOVERY_THREADS", "99")
    with pytest.raises(ValueError, match="SOS_DISCOVERY_THREADS"):
        DiscoveryPolicy.from_env()


def test_profile_null_and_nan_statistics(workspace):
    client, _store = workspace
    dataset = _upload(client, "numbers.csv", b"group,value\na,1.5\na,NaN\nb,\na,1.5\n")
    response = client.get(f"/api/datasets/{dataset['id']}/profile")
    assert response.status_code == 200, response.text
    result = response.json()
    field = next(c for c in result["columns"] if c["name"] == "value")
    assert field["nulls"] == 1
    assert field["nan_count"] == 1
    assert field["unique"] >= 2
    assert field["mean"] is None or math.isfinite(field["mean"])


def test_nonfinite_preview_and_sample_full_exploration_are_json_safe(workspace):
    """원본 NaN·무한대 표시는 구분하고 집계 불능은 JSON NULL로 반환한다."""
    client, store = workspace
    buffer = BytesIO()
    pq.write_table(
        pa.table(
            {
                "group": ["finite", "nan", "positive", "negative"],
                "value": pa.array(
                    [1.25, float("nan"), float("inf"), -float("inf")], type=pa.float64()
                ),
            }
        ),
        buffer,
    )
    dataset = _upload(client, "nonfinite.parquet", buffer.getvalue())
    base = f"/api/datasets/{dataset['id']}"
    preview = client.get(base + "/preview?limit=4")
    assert preview.status_code == 200, preview.text
    values = {row["group"]: row["value"] for row in preview.json()["rows"]}
    assert values == {
        "finite": 1.25,
        "nan": "NaN",
        "positive": "Infinity",
        "negative": "-Infinity",
    }

    spec = {"column": "value", "metric": "max", "group_by": "group"}
    sample = client.post(base + "/explorations", json=spec)
    assert sample.status_code == 200, sample.text
    expected = {"finite": 1.25, "nan": None, "positive": None, "negative": None}
    assert {row["category"]: row["value"] for row in sample.json()["rows"]} == expected

    submitted = client.post(
        "/api/explorations/jobs",
        json={"dataset_id": dataset["id"], "exploration": spec},
    )
    assert submitted.status_code == 202, submitted.text
    assert run_once(store)
    full = client.get(f"/api/explorations/jobs/{submitted.json()['id']}")
    assert full.status_code == 200, full.text
    assert full.json()["status"] == "Succeeded", full.text
    assert {row["category"]: row["value"] for row in full.json()["result"]["rows"]} == expected


def test_exploration_counts_all_categorical_columns(workspace):
    client, store = workspace
    dataset = _upload(client, "categories.csv", b"group,label\na,x\na,\nb,y\n")
    spec = {"column": "label", "metric": "null_count", "group_by": "group"}
    response = client.post(f"/api/datasets/{dataset['id']}/explorations", json=spec)
    assert response.status_code == 200, response.text
    assert {row["category"]: row["value"] for row in response.json()["rows"]} == {
        "a": 1, "b": 0,
    }
    assert client.post(
        f"/api/datasets/{dataset['id']}/explorations",
        json={"column": "label", "metric": "mean"},
    ).status_code == 422
