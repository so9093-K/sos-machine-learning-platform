"""Polars 기반 EDA와 피처 변환을 인프라에 의존하지 않고 처리한다."""

import polars as pl

from .schemas import Transform

MAX_PREVIEW = 100
ALLOWED_CASTS = {
    "String": pl.String,
    "Int64": pl.Int64,
    "Float64": pl.Float64,
    "Boolean": pl.Boolean,
    "Date": pl.Date,
    "Datetime": pl.Datetime,
}

MAX_ICEBERG_ROWS = 50_000
MAX_ICEBERG_MEMORY_BYTES = 64 * 1024 * 1024


def bounded_iceberg_arrow(lf: pl.LazyFrame):
    """한 번의 Iceberg append에서 처리할 데이터 크기를 제한한다."""
    frame = lf.head(MAX_ICEBERG_ROWS + 1).collect()
    if frame.height > MAX_ICEBERG_ROWS:
        raise ValueError("Iceberg import exceeds the supported 50,000-row limit")
    if frame.estimated_size() > MAX_ICEBERG_MEMORY_BYTES:
        raise ValueError("Iceberg import exceeds the supported 64 MiB memory limit")
    return frame.to_arrow()


def safe_preview(lf: pl.LazyFrame, limit: int = 30) -> dict:
    if not 1 <= limit <= MAX_PREVIEW:
        raise ValueError("Preview limit must be between 1 and 100")
    frame = lf.limit(limit).collect()
    return {
        "schema": {c: str(t) for c, t in lf.collect_schema().items()},
        "rows": frame.to_dicts(),
        "returned_rows": frame.height,
    }


def profile(lf: pl.LazyFrame, sample: int | None = 1000) -> dict:
    """전체 데이터를 메모리에 적재하지 않고 샘플 또는 전체 통계를 집계한다."""
    if sample is not None and not 1 <= sample <= 10000:
        raise ValueError("Sample size must be between 1 and 10000")
    selected = lf.head(sample) if sample is not None else lf
    schema = selected.collect_schema()
    aggregations = [pl.len().alias("__rows")]
    for number, (name, dtype) in enumerate(schema.items()):
        col = pl.col(name)
        key = f"c{number}_"
        aggregations.extend(
            [
                col.null_count().alias(key + "null"),
                col.n_unique().alias(key + "unique"),
            ]
        )
        if dtype.is_float():
            aggregations.append(col.is_nan().sum().alias(key + "nan"))
        if dtype.is_numeric():
            aggregations.extend(
                [
                    col.min().alias(key + "min"),
                    col.max().alias(key + "max"),
                    col.mean().alias(key + "mean"),
                ]
            )
    values = selected.select(aggregations).collect().to_dicts()[0]
    columns = []
    for number, (name, dtype) in enumerate(schema.items()):
        key = f"c{number}_"
        info = {
            "name": name,
            "dtype": str(dtype),
            "nulls": values[key + "null"],
            "nan_count": values[key + "nan"] if dtype.is_float() else None,
            "unique": values[key + "unique"],
        }
        if dtype.is_numeric():
            info.update({k: values[key + k] for k in ("min", "max", "mean")})
        columns.append(info)
    return {
        "sampled": sample is not None,
        "sample_limit": sample,
        "row_count": values["__rows"],
        "columns": columns,
    }


def transform(lf: pl.LazyFrame, spec: Transform) -> pl.LazyFrame:
    schema = lf.collect_schema()
    if not set(spec.group_by).issubset(schema.names()):
        raise ValueError("Group column does not exist")
    if spec.order_by:
        if spec.order_by not in schema:
            raise ValueError("Time/order column does not exist")
        # 그룹 안의 시간 순서가 시계열 연산의 의미를 결정한다.
        lf = lf.sort([*spec.group_by, spec.order_by])
    for step in spec.steps:
        columns = lf.collect_schema().names()
        if step.op == "select":
            if not set(step.columns or []).issubset(columns):
                raise ValueError("Selected column does not exist")
            lf = lf.select(step.columns)
            continue
        if step.column not in columns:
            raise ValueError(f"Unknown column: {step.column}")
        col = pl.col(step.column)
        if step.op == "filter_not_null":
            lf = lf.filter(col.is_not_null())
        elif step.op == "fill_null":
            lf = lf.with_columns(col.fill_null(step.value))
        elif step.op == "cast":
            lf = lf.with_columns(col.cast(ALLOWED_CASTS[step.dtype], strict=True))
        elif step.op in ("lag", "rolling_mean"):
            if not spec.order_by:
                raise ValueError("Time-based features require order_by")
            alias = step.alias or f"{step.column}_{step.op}"
            if alias in columns:
                raise ValueError("Feature output column already exists")
            if step.op == "lag":
                expr = col.shift(step.periods)
                lf = lf.with_columns(
                    (expr.over(spec.group_by) if spec.group_by else expr).alias(alias)
                )
            else:
                expr = col.rolling_mean(step.window)
                lf = lf.with_columns(
                    (expr.over(spec.group_by) if spec.group_by else expr).alias(alias)
                )
    return lf
