"""현재 ML 어댑터들이 공유하는 수치형 입력 타입 계약."""

import polars as pl

INTEGER_DTYPES = frozenset(
    (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
)
NUMERIC = INTEGER_DTYPES | frozenset((pl.Float32, pl.Float64))

# Job 제출 시 확정하며, 기존 Job에 버전 필드가 없으면 이전 선택 의미를 보존한다.
TRAINING_SELECTION_LEGACY = "row_priority_bottom_k_v1"
TRAINING_SELECTION_CANONICAL_V1 = "canonical_multiset_v1"
TRAINING_SELECTION_CURRENT = TRAINING_SELECTION_CANONICAL_V1
