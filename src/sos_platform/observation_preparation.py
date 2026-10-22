"""기존 Transform Attempt에서 관측치 UUID를 새 불변 Dataset에 발급한다.

소스 값·해시·순번으로 UUID를 계산하지 않는다. 성공한 Attempt가 게시하는
Parquet 안에서 UUID가 유지되며, 별도로 다시 준비한 Dataset은 새 UUID를 갖는다.
"""

from pathlib import Path
from uuid import uuid4

import polars as pl

from .ml_observation_identity import validate_observation_identity


def materialize_observation_ids(
    source: pl.LazyFrame, output: Path, *, column: str
) -> dict:
    """고정 원본의 모든 행을 보존하고 새 UUIDv4 열을 한번만 물질화한다.

    Args:
        source: 고정 Dataset Reader의 LazyFrame.
        output: 해당 Transform Attempt에 속한 미게시 Parquet 경로.
        column: 신규 관측치 ID 컬럼명.

    Returns:
        dict: 발급된 UUID의 검증 결과와 판본 내 안정성 근거.

    Raises:
        ValueError: ID 컬럼 충돌·빈 원본·출력 검증 실패.

    Notes:
        각 실행에서 새 UUID를 생성하므로 서로 다른 Transform Job 간 동일 ID를
        약속하지 않는다. 후속 단계는 게시된 Dataset Revision을 고정해야 한다.
    """
    if not isinstance(column, str) or not column or len(column) > 256:
        raise ValueError("Observation identity column name is invalid")
    original_schema = source.collect_schema()
    if column in original_schema:
        raise ValueError("Observation identity output column already exists")
    if not original_schema:
        raise ValueError("Observation identity source has no columns")

    def assign(batch: pl.DataFrame) -> pl.DataFrame:
        """Streaming 입력 Batch마다 암호학적 난수 기반 UUIDv4를 발급한다."""
        return batch.with_columns(
            pl.Series(column, [str(uuid4()) for _ in range(batch.height)], dtype=pl.String)
        )

    schema = {**original_schema, column: pl.String}
    enriched = source.map_batches(
        assign,
        schema=schema,
        streamable=True,
        predicate_pushdown=False,
        projection_pushdown=False,
        slice_pushdown=False,
    )
    # 기존 Transform Job의 Attempt 전용 pending 파일로만 기록한다.
    enriched.sink_parquet(output)
    identity = validate_observation_identity(pl.scan_parquet(output), column=column)
    return {
        "observation_identity": {
            **identity,
            "stability": "within_published_dataset_revision",
            "issuance": "random_uuid4_at_materialization",
        },
        "input_rows": identity["validated_rows"],
        "output_rows": identity["validated_rows"],
    }
