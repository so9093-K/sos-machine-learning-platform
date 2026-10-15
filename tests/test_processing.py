"""DB나 객체 저장소 없이 데이터 처리의 의미를 검증한다."""

import polars as pl
import pytest

from sos_platform.processing import bounded_feature_preview, bounded_iceberg_arrow, transform
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


def test_feature_preview_rejects_unbounded_source_before_sorting():
    source = pl.DataFrame({"time": range(100_002), "value": range(100_002)}).lazy()
    spec = Transform.model_validate(
        {"order_by": "time", "steps": [{"op": "lag", "column": "value"}]}
    )
    with pytest.raises(ValueError, match="100,000"):
        bounded_feature_preview(source, spec)


def test_rolling_mean_rejects_non_numeric_inputs_before_execution():
    raw = pl.DataFrame({"time": [1, 2], "label": ["ok", "warn"]}).lazy()
    spec = Transform.model_validate(
        {"order_by": "time", "steps": [{"op": "rolling_mean", "column": "label"}]}
    )
    with pytest.raises(ValueError, match="numeric"):
        transform(raw, spec)


def test_time_features_reject_duplicate_and_null_order_keys():
    duplicates = pl.DataFrame({
        "machine": ["A", "A", "B"], "time": [1, 1, 1], "value": [10, 20, 30],
    }).lazy()
    spec = Transform.model_validate({
        "order_by": "time",
        "group_by": ["machine"],
        "steps": [{"op": "lag", "column": "value"}],
    })
    with pytest.raises(ValueError, match="unique within each group"):
        transform(duplicates, spec)

    null_time = pl.DataFrame({"time": [1, None], "value": [10, 20]}).lazy()
    with pytest.raises(ValueError, match="null values"):
        transform(null_time, Transform.model_validate({
            "order_by": "time", "steps": [{"op": "rolling_mean", "column": "value"}],
        }))

    filtered = Transform.model_validate({
        "order_by": "time",
        "steps": [
            {"op": "filter_not_null", "column": "time"},
            {"op": "lag", "column": "value"},
        ],
    })
    assert transform(null_time, filtered).collect().height == 1


def test_time_sort_occurs_after_prior_cast():
    raw = pl.DataFrame({"time": ["10", "2", "1"], "value": [100, 20, 10]}).lazy()
    spec = Transform.model_validate({
        "order_by": "time",
        "steps": [
            {"op": "cast", "column": "time", "dtype": "Int64"},
            {"op": "lag", "column": "value", "alias": "previous"},
        ],
    })
    result = transform(raw, spec).collect().to_dicts()
    assert [r["time"] for r in result] == [1, 2, 10]
    assert [r["previous"] for r in result] == [None, 10, 20]
