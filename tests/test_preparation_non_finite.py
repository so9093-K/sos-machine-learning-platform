"""정의되지 않은 수치(무한대·NaN)를 결측으로 바꾸는 데이터 준비 단계의 의미를 확인한다.

ML 입력은 무한값을 거부하고 결측은 판본의 학습 중앙값으로 대체한다. 흐름 통계처럼 0으로 나눈
비율이 무한대로 기록된 데이터를 임의의 수로 바꾸지 않고 결측 정책에 맡길 수 있어야 한다.
"""

import math

import polars as pl
import pytest

from sos_platform.errors import InvalidRequestError
from sos_platform.processing import transform
from sos_platform.schemas import Step, Transform


def _frame() -> pl.LazyFrame:
    return pl.DataFrame({
        "Flow Bytes/s": [1.5, math.inf, -math.inf, math.nan, None],
        "packets": [1, 2, 3, 4, 5],
        "host": ["a", "b", "c", "d", "e"],
    }).lazy()


def _apply(column: str) -> pl.DataFrame:
    return transform(_frame(), Transform(steps=[Step(op="null_non_finite", column=column)])).collect()


def test_non_finite_values_become_missing_and_finite_values_stay():
    result = _apply("Flow Bytes/s")
    assert result["Flow Bytes/s"].to_list() == [1.5, None, None, None, None]
    assert result.schema["Flow Bytes/s"] == pl.Float64


def test_integer_columns_are_unchanged():
    assert _apply("packets")["packets"].to_list() == [1, 2, 3, 4, 5]


def test_non_numeric_columns_are_rejected():
    with pytest.raises(InvalidRequestError, match="수치형"):
        _apply("host")
