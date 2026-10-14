"""DB나 객체 저장소 없이 데이터 처리의 의미를 검증한다."""

import polars as pl
import pytest

from sos_platform.processing import bounded_iceberg_arrow, transform
from sos_platform.schemas import Transform


def test_grouped_lag_does_not_cross_machine_boundaries():
    raw = pl.DataFrame(
        {
            "machine_id": ["A", "B", "A", "B", "A", "B"],
            "time": [2, 1, 1, 3, 3, 2],
            "value": [12, 20, 10, 24, 14, 22],
        }
    ).lazy()
    spec = Transform.model_validate(
        {
            "order_by": "time",
            "group_by": ["machine_id"],
            "steps": [
                {"op": "lag", "column": "value", "alias": "previous", "periods": 1},
                {"op": "rolling_mean", "column": "value", "alias": "moving", "window": 2},
            ],
        }
    )
    rows = transform(raw, spec).collect().to_dicts()
    machine_a = [r for r in rows if r["machine_id"] == "A"]
    machine_b = [r for r in rows if r["machine_id"] == "B"]
    assert [r["previous"] for r in machine_a] == [None, 10, 12]
    assert [r["previous"] for r in machine_b] == [None, 20, 22]
    assert machine_a[1]["moving"] == 11
    assert machine_b[1]["moving"] == 21


def test_group_contract_rejects_ambiguous_columns():
    with pytest.raises(ValueError, match="distinct"):
        Transform.model_validate(
            {
                "order_by": "time",
                "group_by": ["machine_id", "machine_id"],
                "steps": [{"op": "lag", "column": "value"}],
            }
        )
    with pytest.raises(ValueError, match="must not"):
        Transform.model_validate(
            {
                "order_by": "time",
                "group_by": ["time"],
                "steps": [{"op": "lag", "column": "value"}],
            }
        )


def test_iceberg_import_is_bounded_before_commit():
    small = pl.DataFrame({"value": [1, 2]}).lazy()
    assert bounded_iceberg_arrow(small).num_rows == 2
    too_large = pl.DataFrame({"value": list(range(50_001))}).lazy()
    with pytest.raises(ValueError, match="50,000"):
        bounded_iceberg_arrow(too_large)


def test_select_must_keep_time_and_group_columns_for_later_windows():
    raw = pl.DataFrame({"machine_id": ["A", "A"], "time": [1, 2], "value": [10, 20]}).lazy()
    spec = Transform.model_validate(
        {
            "group_by": ["machine_id"],
            "order_by": "time",
            "steps": [
                {"op": "select", "columns": ["value"]},
                {"op": "lag", "column": "value"},
            ],
        }
    )
    with pytest.raises(ValueError, match="survive preceding steps"):
        transform(raw, spec)
