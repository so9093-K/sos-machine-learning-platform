"""Polars Feature·Transform 계산을 인프라에 의존하지 않고 처리한다."""

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
    # Row-local 작업은 결과 행만 제한한다. 앞단 전체 행수를 거부할 필요가 없다.
    # Lag/Rolling은 임의로 입력을 자르면 그룹·시간 의미가 깨지므로 별도 취급한다.
    if any(step.op in ("lag", "rolling_mean") for step in spec.steps):
        count = lf.head(MAX_FEATURE_PREVIEW_INPUT_ROWS + 1).select(pl.len()).collect().item()
        if count > MAX_FEATURE_PREVIEW_INPUT_ROWS:
            raise ValueError(
                "Global window preview exceeds synchronous execution budget; "
                "submit a full transform Job"
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


def transform(lf: pl.LazyFrame, spec: Transform) -> pl.LazyFrame:
    schema = lf.collect_schema()
    if not set(spec.group_by).issubset(schema.names()):
        raise ValueError("Group column does not exist")
    if spec.order_by and spec.order_by not in schema:
        raise ValueError("Time/order column does not exist")
    # Window 단계가 연속되면 동일 입력의 전역 검증과 정렬을 반복하지 않는다.
    # 시간·그룹 키 값이 바뀌면 기존 검증은 더 이상 유효하지 않다.
    validated_window_keys = False
    for step in spec.steps:
        current_schema = lf.collect_schema()
        columns = current_schema.names()
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
            if step.column == spec.order_by or step.column in spec.group_by:
                validated_window_keys = False
        elif step.op == "cast":
            lf = lf.with_columns(col.cast(ALLOWED_CASTS[step.dtype], strict=True))
            if step.column == spec.order_by or step.column in spec.group_by:
                validated_window_keys = False
        elif step.op in ("lag", "rolling_mean"):
            if not spec.order_by:
                raise ValueError("Time-based features require order_by")
            if spec.order_by not in columns or not set(spec.group_by).issubset(columns):
                raise ValueError("Time/order and group columns must survive preceding steps")
            if not validated_window_keys:
                # 키 값의 변경은 정렬 순서와 유일성을 무효화한다. 행 필터와
                # 다른 컬럼의 변환은 검증된 키의 유일성을 깨뜨리지 않는다.
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
                validated_window_keys = True
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
