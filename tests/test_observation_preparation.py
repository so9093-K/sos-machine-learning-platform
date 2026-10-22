"""불변 관측치 ID 부여 Transform의 데이터·보안·게시 의미 회귀 테스트."""

from uuid import UUID

import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform.errors import InvalidRequestError
from sos_platform.observation_preparation import materialize_observation_ids
from sos_platform.processing import bounded_feature_preview, transform
from sos_platform.schemas import Transform, WorkRequest
from test_ml_scoring_temporal import scoring_env  # noqa: F401 — 공유 픽스처


def _frame(size: int = 31) -> pl.LazyFrame:
    # 동일 관측치 내용이어도 원본의 서로 다른 행은 다른 불투명 ID가 필요하다.
    return pl.DataFrame({
        "value": [3.0] * size,
        "user_name": ["private"] * size,
        "host_ip": ["192.0.2.17"] * size,
    }).lazy()


def test_published_file_preserves_rows_and_uuid_across_rereads(tmp_path):
    path = tmp_path / "prepared.parquet"
    evidence = materialize_observation_ids(
        _frame(), path, column="observation_uuid"
    )
    first = pl.read_parquet(path)
    second = pl.read_parquet(path)
    assert first.equals(second)
    assert first.height == 31
    assert first["user_name"].to_list() == ["private"] * 31
    assert first["host_ip"].to_list() == ["192.0.2.17"] * 31
    assert first["value"].to_list() == [3.0] * 31
    values = first["observation_uuid"].to_list()
    assert len(set(values)) == 31
    assert all(UUID(value).version == 4 and str(UUID(value)) == value for value in values)
    assert evidence["input_rows"] == evidence["output_rows"] == 31
    assert evidence["observation_identity"]["stability"] == (
        "within_published_dataset_revision"
    )
    assert evidence["observation_identity"]["issuance"] == "random_uuid4_at_materialization"


def test_separate_materializations_have_distinct_ids(tmp_path):
    first = tmp_path / "a.parquet"
    second = tmp_path / "b.parquet"
    materialize_observation_ids(_frame(), first, column="observation_uuid")
    materialize_observation_ids(_frame(), second, column="observation_uuid")
    a = pl.read_parquet(first)["observation_uuid"].to_list()
    b = pl.read_parquet(second)["observation_uuid"].to_list()
    assert not set(a) & set(b)


def test_streaming_generation_supports_large_inputs(tmp_path):
    n = 50_021
    source = pl.DataFrame({"counter": list(range(n))}).lazy()
    dest = tmp_path / "large.parquet"
    evidence = materialize_observation_ids(source, dest, column="observation_uuid")
    result = pl.scan_parquet(dest)
    assert evidence["output_rows"] == n
    assert result.select(pl.col("observation_uuid").n_unique()).collect().item() == n
    assert result.select(pl.col("counter").max()).collect().item() == n - 1


def test_existing_identity_column_and_empty_data_rejected(tmp_path):
    source = pl.DataFrame({"observation_uuid": ["x"]}).lazy()
    with pytest.raises(ValueError, match="already exists"):
        materialize_observation_ids(
            source, tmp_path / "existing.parquet", column="observation_uuid"
        )
    with pytest.raises(ValueError, match="nonempty"):
        materialize_observation_ids(
            pl.DataFrame({"value": pl.Series([], dtype=pl.Float64)}).lazy(),
            tmp_path / "empty.parquet", column="observation_uuid",
        )


def test_preparation_must_be_standalone_transform_spec():
    accepted = Transform(steps=[], observation_id_column="observation_uuid")
    assert accepted.observation_id_column == "observation_uuid"
    with pytest.raises(ValidationError, match="requires steps"):
        Transform(steps=[])
    with pytest.raises(ValidationError, match="standalone"):
        Transform(steps=[{"op": "select", "columns": ["x"]}],
                  observation_id_column="observation_uuid")
    with pytest.raises(ValidationError, match="standalone"):
        Transform(steps=[], order_by="time", observation_id_column="observation_uuid")
    with pytest.raises(ValidationError, match="standalone"):
        Transform(steps=[], group_by=["host"], observation_id_column="observation_uuid")
    req = WorkRequest(
        dataset_id="data", kind="transform",
        transform={"steps": [], "observation_id_column": "observation_uuid"},
    )
    assert req.transform.observation_id_column == "observation_uuid"


def test_no_ephemeral_uuid_from_preview_or_processing():
    spec = Transform(steps=[], observation_id_column="observation_uuid")
    with pytest.raises(InvalidRequestError, match="published Transform Job"):
        bounded_feature_preview(_frame(), spec)
    with pytest.raises(InvalidRequestError, match="published Transform Job"):
        transform(_frame(), spec)


def test_existing_transform_steps_still_work():
    spec = Transform(steps=[{"op": "select", "columns": ["value"]}])
    data = transform(_frame(2), spec).collect()
    assert data.columns == ["value"]
    assert data.height == 2


def test_transform_job_http_submission_pins_source_revision_and_uses_existing_permission(
    monkeypatch, tmp_path,
):
    """UUID 준비 Job도 기존 Dataset 읽기·Job 제출 권한, Job/Attempt 계약을 사용한다."""
    from unittest.mock import Mock

    from fastapi.testclient import TestClient

    from sos_platform import api
    from sos_platform.security import Actor, IdentitySettings

    dataset = {"id": "raw-dataset", "version": "raw-v1", "owner_actor": "alice"}
    store = Mock()
    store.get_dataset.return_value = dataset

    def created(**kwargs):
        return {
            "id": "prepare-job", "kind": kwargs["kind"], "dataset_id": kwargs["dataset_id"],
            "spec": kwargs["spec"], "status": "Queued", "cancel_requested": False,
            "result": None, "error": None, "attempts": [],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }

    store.create_job.side_effect = created
    monkeypatch.setattr(api, "Store", lambda home: store)
    app = api.create_app(tmp_path, identity=IdentitySettings("development", {}))
    app.dependency_overrides[api.CREATE_JOB_DEP.dependency] = (
        lambda: Actor("alice", "human", frozenset({"researcher"}))
    )
    body = {
        "dataset_id": dataset["id"], "kind": "transform",
        "transform": {"steps": [], "observation_id_column": "observation_uuid"},
    }
    with TestClient(app) as client:
        created_job = client.post("/api/jobs", json=body)
        assert created_job.status_code == 202, created_job.text
        submitted = store.create_job.call_args.kwargs
        assert submitted["kind"] == "transform"
        assert submitted["dataset_id"] == dataset["id"]
        assert submitted["spec"]["transform"]["observation_id_column"] == (
            "observation_uuid"
        )
        assert submitted["spec"]["transform"]["steps"] == []

        dataset["owner_actor"] = "bob"
        denied = client.post("/api/jobs", json=body)
        assert denied.status_code == 404
        dataset["owner_actor"] = "alice"

        mixed = client.post("/api/jobs", json={
            **body,
            "transform": {
                "steps": [{"op": "select", "columns": ["value"]}],
                "observation_id_column": "observation_uuid",
            },
        })
        assert mixed.status_code == 422


def test_prepared_dataset_scoring_keeps_observation_identity_across_time_scopes(
    monkeypatch, scoring_env, tmp_path
):
    """Dataset 준비 → 고정 판본 → Calibration/Test 점수화에서 UUID가 유지된다."""
    import hashlib
    from contextlib import contextmanager

    from sos_platform import ml_scoring
    from test_ml_scoring_temporal import scope, spec

    store, model, dataset, _, _ = scoring_env
    raw = pl.DataFrame({
        "ts": [f"2026-01-01T{hour:02d}:00:00Z" for hour in range(9)],
        "count": [float(value) for value in range(9)],
    })
    prepared_path = tmp_path / "prepared.parquet"
    materialize_observation_ids(
        raw.lazy(), prepared_path, column="observation_uuid"
    )
    # 실제 Dataset 게시 후에는 원본 바이트의 불변 판본을 참조하게 된다.
    dataset["version"] = hashlib.sha256(prepared_path.read_bytes()).hexdigest()
    model["dataset_version"] = dataset["version"]

    @contextmanager
    def scan_published(_store, pinned):
        assert pinned["version"] == dataset["version"]
        yield pl.scan_parquet(prepared_path)

    monkeypatch.setattr(ml_scoring, "pinned_scan_dataset", scan_published)
    originals = pl.read_parquet(prepared_path)["observation_uuid"].to_list()
    for partition, expected in (("calibration", originals[4:7]), ("test", originals[7:9])):
        request = spec(partition)
        request["dataset_version"] = dataset["version"]
        request["observation_id_column"] = "observation_uuid"
        output = tmp_path / f"{partition}.scores.parquet"
        scored = ml_scoring.run_dataset_scoring(store, dataset, request, output)
        assert scored["row_count"] == len(expected)
        assert pl.read_parquet(output)["observation_id"].to_list() == expected
        assert scored["score_summary"]["observation_identity"]["dataset_version"] == (
            dataset["version"]
        )
