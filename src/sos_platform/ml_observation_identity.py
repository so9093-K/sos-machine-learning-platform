"""Scoring 결과를 불변 원본 관측치에 연결하는 선택적 불투명 ID 계약.

업무 식별자·사용자명·호스트·시간값의 해시를 만들지 않는다. 입력 Dataset에
미리 생성·고정된 UUIDv4 문자열만 받으며, 선택 범위 내 null/중복을 거절한다.
이 형식 검증은 개인정보 여부를 보장하지 않으므로 원본/Scoring 권한이 별도로 필요하다.
"""

import polars as pl

OPAQUE_UUID4_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def validate_observation_identity(
    staged: pl.LazyFrame, *, column: str, selected_rows: int | None = None
) -> dict:
    """선택 구간의 UUIDv4 관측치 서명이 정확하고 유일한지 검증한다.

    Args:
        staged: 모델 입력과 관측치 ID를 포함한 고정 Dataset의 선택 구간.
        column: 원본 Dataset에서 고정한 UUIDv4 String 컬럼명.
        selected_rows: 이미 확인한 선택 행 수. 없으면 직접 계산한다.

    Returns:
        dict: 유효성을 확인한 식별자의 형식·컬럼·행 수 계약.

    Raises:
        ValueError: 타입, 형식, null, 중복 또는 행 수가 유효하지 않은 경우.
    """
    schema = staged.collect_schema()
    if column not in schema:
        raise ValueError("Observation identity column is missing")
    if schema[column] != pl.String:
        raise ValueError("Observation identity must be a UUIDv4 String column")
    values = pl.col(column)
    summary = staged.select(
        pl.len().alias("rows"),
        values.null_count().alias("null_rows"),
        ((~values.str.contains(OPAQUE_UUID4_PATTERN)).fill_null(True))
        .sum().alias("invalid_rows"),
        values.n_unique().alias("distinct_rows"),
    ).collect().row(0, named=True)
    total = summary["rows"]
    if total == 0:
        raise ValueError("Observation identity requires nonempty selected data")
    if selected_rows is not None and total != selected_rows:
        raise ValueError("Observation identity row count changed")
    if summary["null_rows"] or summary["invalid_rows"]:
        raise ValueError("Observation identity contains null or non-UUIDv4 values")
    if summary["distinct_rows"] != total:
        raise ValueError("Observation identity contains duplicate values")
    return {
        "kind": "opaque_uuid4",
        "source_column": column,
        "validated_rows": total,
        "unique_within": "selected_scoring_scope",
    }
