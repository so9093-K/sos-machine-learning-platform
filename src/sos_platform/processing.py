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
MAX_FEATURE_PREVIEW_INPUT_ROWS = 100_000


def bounded_feature_preview(lf: pl.LazyFrame, spec: Transform) -> dict:
    """사용자 요청 API가 큰 입력의 정렬·윈도 계산으로 장시간 점유되지 않게 한다."""
    count = lf.head(MAX_FEATURE_PREVIEW_INPUT_ROWS + 1).select(pl.len()).collect().item()
    if count > MAX_FEATURE_PREVIEW_INPUT_ROWS:
        raise ValueError(
            "Feature preview exceeds 100,000 input rows; submit a full transform Job instead"
        )
    return safe_preview(transform(lf, spec), limit=30)


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
    if spec.order_by and spec.order_by not in schema:
        raise ValueError("Time/order column does not exist")
    # 연속된 시간 피처 단계 사이에는 시간·그룹 키가 바뀌지 않는다.
    # 값·행·컬럼을 바꾸는 단계가 등장하면 검증을 다시 수행한다.
    time_keys_validated = False
    for step in spec.steps:
        current_schema = lf.collect_schema()
        columns = current_schema.names()
        if step.op == "select":
            if not set(step.columns or []).issubset(columns):
                raise ValueError("Selected column does not exist")
            lf = lf.select(step.columns)
            time_keys_validated = False
            continue
        if step.column not in columns:
            raise ValueError(f"Unknown column: {step.column}")
        col = pl.col(step.column)
        if step.op == "filter_not_null":
            lf = lf.filter(col.is_not_null())
            time_keys_validated = False
        elif step.op == "fill_null":
            lf = lf.with_columns(col.fill_null(step.value))
            time_keys_validated = False
        elif step.op == "cast":
            lf = lf.with_columns(col.cast(ALLOWED_CASTS[step.dtype], strict=True))
            time_keys_validated = False
        elif step.op in ("lag", "rolling_mean"):
            if not spec.order_by:
                raise ValueError("Time-based features require order_by")
            if spec.order_by not in columns or not set(spec.group_by).issubset(columns):
                raise ValueError("Time/order and group columns must survive preceding steps")
            # 이전 단계가 입력의 행 또는 시간 키를 변경했다면 검증·정렬을 반복한다.
            # 연속 Lag/Rolling은 키를 변경하지 않으므로 중복 스캔을 피할 수 있다.
            if not time_keys_validated:
                if lf.select(pl.col(spec.order_by).is_null().any()).collect().item():
                    raise ValueError("Time/order column contains null values")
                keys = [*spec.group_by, spec.order_by]
                duplicates = (
                    lf.group_by(keys)
                    .agg(pl.len().alias("__sos_time_count"))
                    .filter(pl.col("__sos_time_count") > 1)
                    .limit(1)
                    .collect()
                )
                if duplicates.height:
                    raise ValueError("Time/order column must be unique within each group")
                lf = lf.sort(keys)
                time_keys_validated = True
            if step.op == "rolling_mean" and not current_schema[step.column].is_numeric():
                raise ValueError("Rolling Mean requires a numeric column")
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
