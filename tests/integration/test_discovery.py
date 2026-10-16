"""DuckDB Discovery의 분석 의미·권한·Job·Iceberg Snapshot 계약을 검증한다."""

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
    client, _store = workspace
    dataset = _upload(client, "categories.csv", b"group,label\na,x\na,\nb,y\n")
    spec = {"column": "label", "metric": "null_count", "group_by": "group"}
    response = client.post(f"/api/datasets/{dataset['id']}/explorations", json=spec)
    assert response.status_code == 200, response.text
    assert {row["category"]: row["value"] for row in response.json()["rows"]} == {
        "a": 1,
        "b": 0,
    }
    assert (
        client.post(
            f"/api/datasets/{dataset['id']}/explorations",
            json={"column": "label", "metric": "mean"},
        ).status_code
        == 422
    )


def test_discovery_query_spill_directory_is_owned_and_released(workspace, monkeypatch):
    """실행 슬롯 2개에서 동시 요청의 임시 공간과 종료 시 회수를 검증한다."""
    monkeypatch.setenv("SOS_DISCOVERY_MAX_ACTIVE", "2")
    client, store = workspace
    dataset = _upload(client, "scratch.csv", b"value\n1\n2\n")
    scratch_root = store.home / "cache" / "discovery-tmp"
    with source_connection(store, dataset) as first:
        with source_connection(store, dataset) as second:
            assert first.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 2
            assert second.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 2
            assert len(list(scratch_root.glob("query-*"))) == 2
        assert len(list(scratch_root.glob("query-*"))) == 1
    assert not list(scratch_root.glob("query-*"))


def _parquet_payload(table: pa.Table) -> bytes:
    """계약별 Parquet 입력을 결정적 바이트로 생성한다."""
    buffer = BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def test_decimal_precision_is_preserved_across_sample_profile_and_worker(workspace):
    """큰 DECIMAL의 MIN/MAX는 샘플과 Worker에서 정확히 같은 십진 문자열이다."""
    client, store = workspace
    high = Decimal("9007199254740993.125000")
    low = Decimal("9007199254740992.500000")
    table = pa.table(
        {
            "group": ["high", "low"],
            "amount": pa.array([high, low], type=pa.decimal128(30, 6)),
        }
    )
    dataset = _upload(client, "precise.parquet", _parquet_payload(table))
    base = f"/api/datasets/{dataset['id']}"
    preview = client.get(base + "/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["schema"]["amount"] == "Decimal(30,6)"
    assert [row["amount"] for row in preview.json()["rows"]] == [str(high), str(low)]
    profile = client.get(base + "/profile")
    assert profile.status_code == 200, profile.text
    amount = next(col for col in profile.json()["columns"] if col["name"] == "amount")
    assert amount["min"] == str(low)
    assert amount["max"] == str(high)

    spec = {"column": "amount", "metric": "max", "group_by": "group"}
    sample = client.post(base + "/explorations", json=spec)
    assert sample.status_code == 200, sample.text
    exact = {"high": str(high), "low": str(low)}
    assert {row["category"]: row["value"] for row in sample.json()["rows"]} == exact
    submitted = client.post(
        "/api/explorations/jobs", json={"dataset_id": dataset["id"], "exploration": spec}
    )
    assert submitted.status_code == 202, submitted.text
    assert run_once(store)
    job = client.get(f"/api/explorations/jobs/{submitted.json()['id']}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "Succeeded", job.text
    assert {row["category"]: row["value"] for row in job.json()["result"]["rows"]} == exact


def test_binary_parquet_preview_and_null_group_are_json_safe(workspace):
    """임의 bytes 및 실제 NULL 그룹을 Preview와 샘플·전체 Job에서 구별한다."""
    client, store = workspace
    table = pa.table(
        {
            "binary": pa.array([b"\xff", None, b"\x00"], type=pa.binary()),
            "value": [1, 2, 3],
        }
    )
    dataset = _upload(client, "binary.parquet", _parquet_payload(table))
    base = f"/api/datasets/{dataset['id']}"
    preview = client.get(base + "/preview")
    assert preview.status_code == 200, preview.text
    assert [row["binary"] for row in preview.json()["rows"]] == [
        "base64:/w==",
        None,
        "base64:AA==",
    ]
    spec = {"column": "value", "metric": "count", "group_by": "binary"}
    sample = client.post(base + "/explorations", json=spec)
    assert sample.status_code == 200, sample.text
    exact = {"base64:/w==": 1, None: 1, "base64:AA==": 1}
    assert {row["category"]: row["value"] for row in sample.json()["rows"]} == exact
    submitted = client.post(
        "/api/explorations/jobs", json={"dataset_id": dataset["id"], "exploration": spec}
    )
    assert submitted.status_code == 202, submitted.text
    assert run_once(store)
    job = client.get(f"/api/explorations/jobs/{submitted.json()['id']}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "Succeeded", job.text
    assert {row["category"]: row["value"] for row in job.json()["result"]["rows"]} == exact


def test_nonfinite_groups_sort_after_normalization_before_limit(workspace, monkeypatch):
    """NaN 그룹이 우선 표시되어 유효한 숫자 그룹을 숨기지 않는다."""
    monkeypatch.setenv("SOS_DISCOVERY_MAX_GROUPS", "5")
    client, store = workspace
    table = pa.table(
        {
            "group": [f"bad_{i}" for i in range(5)] + ["good_a", "good_b"],
            "value": pa.array([float("nan")] * 5 + [7.0, 8.0], type=pa.float64()),
        }
    )
    dataset = _upload(client, "ranked.parquet", _parquet_payload(table))
    spec = {"column": "value", "metric": "max", "group_by": "group"}
    sample = client.post(f"/api/datasets/{dataset['id']}/explorations", json=spec)
    assert sample.status_code == 200, sample.text
    rows = sample.json()["rows"]
    assert sample.json()["truncated"] is True
    assert len(rows) == 5
    assert [(row["category"], row["value"]) for row in rows[:2]] == [
        ("good_b", 8.0),
        ("good_a", 7.0),
    ]
    assert all(row["value"] is None for row in rows[2:])
    submitted = client.post(
        "/api/explorations/jobs", json={"dataset_id": dataset["id"], "exploration": spec}
    )
    assert submitted.status_code == 202
    assert run_once(store)
    job = client.get(f"/api/explorations/jobs/{submitted.json()['id']}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "Succeeded"
    assert job.json()["result"]["rows"] == rows


def test_unsigned_integer_and_zoned_timestamp_keep_dtype_and_json_meaning(workspace):
    """부호·폭·시간대를 표시하며 브라우저에서 큰 정수 정밀도를 잃지 않는다."""
    client, _store = workspace
    huge = 18446744073709551615
    instant = datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=9)))
    table = pa.table(
        {
            "counter": pa.array([huge], type=pa.uint64()),
            "observed": pa.array([instant], type=pa.timestamp("us", tz="Asia/Seoul")),
        }
    )
    dataset = _upload(client, "typed.parquet", _parquet_payload(table))
    base = f"/api/datasets/{dataset['id']}"
    preview = client.get(base + "/preview")
    assert preview.status_code == 200, preview.text
    row = preview.json()["rows"][0]
    assert preview.json()["schema"]["counter"] == "UInt64"
    assert preview.json()["schema"]["observed"] == "Datetime(time_unit='us', time_zone='UTC')"
    assert row["counter"] == str(huge)
    assert row["observed"].startswith("2023-12-31T15:00:00")
    assert row["observed"].endswith("+00:00")
    result = client.post(base + "/explorations", json={"column": "counter", "metric": "max"})
    assert result.status_code == 200, result.text
    assert result.json()["rows"][0]["value"] == str(huge)
    profile = client.get(base + "/profile")
    assert profile.status_code == 200, profile.text
    counter = next(c for c in profile.json()["columns"] if c["name"] == "counter")
    assert counter["max"] == str(huge)


def test_dataset_query_projects_filters_sorts_and_validates_columns(workspace):
    client, _store = workspace
    dataset = _upload(
        client, "wide.csv", b"group,value,hidden\na,3,secret\nb,1,private\na,2,internal\n"
    )
    path = f"/api/datasets/{dataset['id']}/query"
    result = client.post(
        path,
        json={
            "columns": ["group", "value"],
            "filters": [
                {"column": "group", "op": "eq", "value": "a"},
                {"column": "value", "op": "gte", "value": 2},
            ],
            "order_by": "value",
            "descending": True,
            "limit": 2,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json() == {
        "schema": {"group": "String", "value": "Int64"},
        "rows": [{"group": "a", "value": 3}, {"group": "a", "value": 2}],
        "returned_rows": 2,
    }
    assert client.post(path, json={"columns": ["missing; DELETE FROM datasets"]}).status_code == 422
    assert (
        client.post(
            path, json={"filters": [{"column": "hidden", "op": "eq", "value": "x' OR TRUE --"}]}
        ).status_code
        == 200
    )
    assert client.post(path, json={"limit": 101}).status_code == 422
    assert client.get(f"/api/datasets/{dataset['id']}/preview").status_code == 200



def test_query_handles_wide_schemas_and_invalid_typed_comparisons(workspace):
    client, _store = workspace
    buffer = BytesIO()
    pq.write_table(
        pa.table({f"sensor_{index:03d}": pa.array([index, index + 1])
                  for index in range(320)}),
        buffer,
    )
    dataset = _upload(client, "wide-schema.parquet", buffer.getvalue())
    path = f"/api/datasets/{dataset['id']}/query"
    projected = client.post(path, json={
        "columns": ["sensor_319", "sensor_000"], "limit": 1,
        "order_by": "sensor_319", "descending": True,
    })
    assert projected.status_code == 200, projected.text
    assert projected.json()["rows"] == [{"sensor_319": 320, "sensor_000": 1}]
    all_columns = client.post(path, json={"columns": [
        f"sensor_{index:03d}" for index in range(320)], "limit": 1})
    assert all_columns.status_code == 200, all_columns.text
    assert len(all_columns.json()["schema"]) == 320
    incompatible = client.post(path, json={"filters": [
        {"column": "sensor_000", "op": "gte", "value": "not-a-number"}]})
    assert incompatible.status_code == 422
    assert incompatible.json()["code"] == "SOS-API-VALIDATION"
