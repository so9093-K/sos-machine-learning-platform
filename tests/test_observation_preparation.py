"""불변 관측치 ID 부여 Transform의 데이터·보안·게시 의미 회귀 테스트."""

from uuid import UUID

import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform.errors import InvalidRequestError
from sos_platform.observation_preparation import materialize_observation_ids
from sos_platform.processing import bounded_feature_preview, transform
from sos_platform.schemas import Transform, WorkRequest


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
