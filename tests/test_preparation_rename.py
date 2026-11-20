"""데이터 준비의 컬럼 이름 변경 단계가 값·자료형을 보존하고 기존 컬럼·키를 덮어쓰지 않는지 확인한다."""

import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform.errors import InvalidRequestError
from sos_platform.processing import bounded_feature_preview, transform
from sos_platform.schemas import Step, Transform


def _frame() -> pl.LazyFrame:
    return pl.DataFrame({
        " IO-Bytes ": [1, 2, 3],
        "host": ["a", "a", "b"],
        "ts": [1, 2, 1],
    }).lazy()


def test_rename_keeps_values_and_dtype_in_preview_and_job():
    spec = Transform(steps=[Step(op="rename", column=" IO-Bytes ", alias="io_bytes")])
    preview = bounded_feature_preview(_frame(), spec, max_seconds=5, memory_mib=64)
    # 미리보기 스키마는 모델 입력 재확인에 쓰이므로 Job 결과와 같은 이름·자료형이어야 한다.
    assert preview["schema"] == {"io_bytes": "Int64", "host": "String", "ts": "Int64"}
    result = transform(_frame(), spec).collect()
    assert result.columns == ["io_bytes", "host", "ts"]
    assert result["io_bytes"].to_list() == [1, 2, 3]


def test_rename_then_cast_uses_the_new_name():
    spec = Transform(steps=[
        Step(op="rename", column=" IO-Bytes ", alias="io_bytes"),
        Step(op="cast", column="io_bytes", dtype="Float64"),
    ])
    assert transform(_frame(), spec).collect_schema()["io_bytes"] == pl.Float64


def test_rename_rejects_overwriting_an_existing_column():
    spec = Transform(steps=[Step(op="rename", column=" IO-Bytes ", alias="host")])
    with pytest.raises(InvalidRequestError, match="이미 있습니다"):
        transform(_frame(), spec)


def test_renaming_a_window_key_is_rejected_before_window_steps():
    spec = Transform(
        steps=[Step(op="rename", column="ts", alias="time"),
               Step(op="lag", column=" IO-Bytes ")],
        order_by="ts", group_by=["host"],
    )
    with pytest.raises(InvalidRequestError, match="사라졌습니다"):
        transform(_frame(), spec)


@pytest.mark.parametrize("alias", [None, "", "   ", " IO-Bytes "])
def test_rename_requires_a_new_non_blank_name(alias):
    with pytest.raises(ValidationError):
        Step(op="rename", column=" IO-Bytes ", alias=alias)
