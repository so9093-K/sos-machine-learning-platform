"""현재 ML 어댑터들이 공유하는 수치형 입력 타입 계약."""

import polars as pl

INTEGER_DTYPES = frozenset(
    (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
)
NUMERIC = INTEGER_DTYPES | frozenset((pl.Float32, pl.Float64))
