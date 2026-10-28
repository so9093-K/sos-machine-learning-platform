"""Polars Feature·Transform 계산을 인프라에 의존하지 않고 처리한다."""

import polars as pl

from .errors import InvalidRequestError
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
            raise InvalidRequestError(
                "Lag·Rolling 미리보기는 입력 행 수가 즉시 계산 한도를 넘습니다. "
                "전체 변환 Job으로 실행하세요."
            )
    return safe_preview(transform(lf, spec), limit=30)


def bounded_iceberg_arrow(lf: pl.LazyFrame):
    """한 번의 Iceberg append에서 처리할 데이터 크기를 제한한다."""
    frame = lf.head(MAX_ICEBERG_ROWS + 1).collect()
    if frame.height > MAX_ICEBERG_ROWS:
        raise InvalidRequestError("Iceberg 적재는 최대 50,000행까지 지원합니다.")
    if frame.estimated_size() > MAX_ICEBERG_MEMORY_BYTES:
        raise InvalidRequestError("Iceberg 적재 데이터가 지원 메모리 한도 64 MiB를 초과합니다.")
    return frame.to_arrow()


def safe_preview(lf: pl.LazyFrame, limit: int = 30) -> dict:
    if not 1 <= limit <= MAX_PREVIEW:
        raise InvalidRequestError("미리보기 행 수는 1~100 사이여야 합니다.")
    frame = lf.limit(limit).collect()
    return {
        "schema": {c: str(t) for c, t in lf.collect_schema().items()},
        "rows": frame.to_dicts(),
        "returned_rows": frame.height,
    }


def transform(lf: pl.LazyFrame, spec: Transform) -> pl.LazyFrame:
    if spec.observation_id_column is not None:
        # Preview는 발급 뒤 게시된 Dataset 판본이 아니므로 임시 UUID를 보여주지 않는다.
        raise InvalidRequestError("관측 식별자를 만들려면 완료된 변환 Job이 필요합니다.")
    schema = lf.collect_schema()
    if not set(spec.group_by).issubset(schema.names()):
        raise InvalidRequestError("그룹 컬럼이 Dataset에 없습니다.")
    if spec.order_by and spec.order_by not in schema:
        raise InvalidRequestError("시간·정렬 컬럼이 Dataset에 없습니다.")
    # Window 단계가 연속되면 동일 입력의 전역 검증과 정렬을 반복하지 않는다.
    # 시간·그룹 키 값이 바뀌면 기존 검증은 더 이상 유효하지 않다.
    validated_window_keys = False
    for step in spec.steps:
        current_schema = lf.collect_schema()
        columns = current_schema.names()
        if step.op == "select":
            if not set(step.columns or []).issubset(columns):
                raise InvalidRequestError("선택한 컬럼이 Dataset에 없습니다.")
            lf = lf.select(step.columns)
            continue
        if step.column not in columns:
            raise InvalidRequestError(f"컬럼이 Dataset에 없습니다: {step.column}")
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
                raise InvalidRequestError("Lag·Rolling 변환에는 시간·정렬 컬럼이 필요합니다.")
            if spec.order_by not in columns or not set(spec.group_by).issubset(columns):
                raise InvalidRequestError(
                    "앞선 변환 단계에서 시간·정렬 컬럼이나 그룹 컬럼이 사라졌습니다."
                )
            if not validated_window_keys:
                # 키 값의 변경은 정렬 순서와 유일성을 무효화한다. 행 필터와
                # 다른 컬럼의 변환은 검증된 키의 유일성을 깨뜨리지 않는다.
                if lf.select(pl.col(spec.order_by).is_null().any()).collect().item():
                    raise InvalidRequestError("시간·정렬 컬럼에 빈 값이 있습니다.")
                keys = [*spec.group_by, spec.order_by]
                duplicates = (
                    lf.group_by(keys)
                    .agg(pl.len().alias("__sos_time_count"))
                    .filter(pl.col("__sos_time_count") > 1)
                    .limit(1)
                    .collect()
                )
                if duplicates.height:
                    raise InvalidRequestError(
                        "시간·정렬 컬럼 값은 그룹 안에서 중복되지 않아야 합니다."
                    )
                lf = lf.sort(keys)
                validated_window_keys = True
            if step.op == "rolling_mean" and not current_schema[step.column].is_numeric():
                raise InvalidRequestError("Rolling Mean은 수치형 컬럼에만 적용할 수 있습니다.")
            alias = step.alias or f"{step.column}_{step.op}"
            if alias in columns:
                raise InvalidRequestError("생성할 Feature 컬럼 이름이 이미 있습니다.")
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
