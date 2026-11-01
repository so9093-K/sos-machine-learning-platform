"""Polars Feature·Transform 계산을 인프라에 의존하지 않고 처리한다."""

import time
from dataclasses import dataclass

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

# Lag·Rolling 미리보기가 Window 계산에 쓰는 정렬 키 순서의 앞부분 행 수.
PREVIEW_WINDOW_ROWS = 10_000
_PREVIEW_BATCH_ROWS = 65_536


@dataclass(frozen=True)
class PreviewScope:
    """동기 미리보기의 계산 범위.

    Window 단계는 정렬 키 순서의 앞 `rows`행만 계산한다. 그룹 안의 이전 행은 전체 정렬에서도 앞에
    있으므로 결과 앞부분은 전체 변환과 같다. 입력을 한 번 순회하는 비용이 `deadline`(monotonic 시각)을
    넘으면 중단한다. 키 유일성처럼 전체 데이터가 필요한 검증은 변환 Job이 확정한다.
    """

    rows: int
    deadline: float


def bounded_feature_preview(lf: pl.LazyFrame, spec: Transform, *, max_seconds: float) -> dict:
    """변환 결과의 앞부분을 입력 크기와 관계없이 같은 의미로 계산한다.

    계산 비용은 입력 행 수가 아니라 한 요청의 계산 시간으로 제한한다. 넘으면 같은 변환을 Job으로
    실행하도록 안내한다.
    """
    scope = PreviewScope(rows=PREVIEW_WINDOW_ROWS, deadline=time.monotonic() + max_seconds)
    return safe_preview(transform(lf, spec, preview=scope), limit=30)


def _leading_rows(
    lf: pl.LazyFrame, keys: list[str], order_by: str, rows: int, deadline: float,
) -> pl.DataFrame:
    """정렬 키 순서의 앞 rows행. 배치마다 지금까지의 앞부분과 합쳐 다시 자르므로 메모리는 배치·rows에 비례한다."""
    leading = None
    for batch in lf.collect_batches(chunk_size=_PREVIEW_BATCH_ROWS, maintain_order=False):
        if time.monotonic() > deadline:
            raise InvalidRequestError(
                "Lag·Rolling 미리보기 계산이 즉시 계산 시간 한도를 넘었습니다. 전체 변환 Job으로 실행하세요."
            )
        if batch[order_by].null_count():
            raise InvalidRequestError("시간·정렬 컬럼에 빈 값이 있습니다.")
        combined = batch if leading is None else pl.concat([leading, batch])
        leading = combined.sort(keys).head(rows)
    return leading if leading is not None else lf.head(0).collect()


def safe_preview(lf: pl.LazyFrame, limit: int = 30) -> dict:
    if not 1 <= limit <= MAX_PREVIEW:
        raise InvalidRequestError("미리보기 행 수는 1~100 사이여야 합니다.")
    frame = lf.limit(limit).collect()
    return {
        "schema": {c: str(t) for c, t in lf.collect_schema().items()},
        "rows": frame.to_dicts(),
        "returned_rows": frame.height,
    }


def transform(lf: pl.LazyFrame, spec: Transform, *, preview: PreviewScope | None = None) -> pl.LazyFrame:
    """변환 계획. `preview`가 있으면 Window 단계를 정렬 키 순서의 앞부분만으로 계산한다."""
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
    sorted_once = False
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
                keys = [*spec.group_by, spec.order_by]
                if preview is not None:
                    # 다시 정렬하면 앞부분이 전체 정렬의 앞부분과 달라진다. 결과를 추정하지 않는다.
                    if sorted_once:
                        raise InvalidRequestError(
                            "정렬 키를 바꾼 뒤 다시 정렬하는 변환은 전체 변환 Job으로 확인하세요."
                        )
                    # 경계의 키 중복도 드러나도록 한 행 더 읽고 확인한 뒤 자른다.
                    leading = _leading_rows(
                        lf, keys, spec.order_by, preview.rows + 1, preview.deadline,
                    )
                    checked = leading.lazy()
                else:
                    if lf.select(pl.col(spec.order_by).is_null().any()).collect().item():
                        raise InvalidRequestError("시간·정렬 컬럼에 빈 값이 있습니다.")
                    checked = lf
                duplicates = (
                    checked.group_by(keys)
                    .agg(pl.len().alias("__sos_time_count"))
                    .filter(pl.col("__sos_time_count") > 1)
                    .limit(1)
                    .collect()
                )
                if duplicates.height:
                    raise InvalidRequestError(
                        "시간·정렬 컬럼 값은 그룹 안에서 중복되지 않아야 합니다."
                    )
                lf = leading.head(preview.rows).lazy() if preview is not None else lf.sort(keys)
                validated_window_keys = True
                sorted_once = True
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
