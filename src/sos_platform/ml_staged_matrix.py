"""디스크 Memmap 학습 행렬. SOS 학습과 외부 생산 Pipeline이 같은 기록·전처리 규칙을 쓴다.

입력은 Polars LazyFrame을 배치로 읽어 검증하며 Attempt(또는 생산 Run) 소유 Stage의 Memmap에 기록한다.
학습 중앙값과 전처리 입력도 열·배치 단위로 계산해 전체 입력을 Python 객체나 추가 메모리 사본으로
만들지 않는다. 모델 적합(fit) 자체가 요구하는 메모리는 알고리즘 Runtime의 실행 계약이다.
"""

from pathlib import Path

import numpy as np
import polars as pl

from .ml_if_preprocessing import FittedIFMedianImputer, _train_median
from .ml_input import INTEGER_DTYPES

_BATCH_ROWS = 8192


def write_feature_matrix(
    scan: pl.LazyFrame,
    features: list[str],
    schema: dict,
    path: Path,
    *,
    missing_policy: str,
) -> np.memmap:
    """Feature 값을 검증하며 행 순서대로 float64 Memmap에 기록한다.

    Args:
        scan: Feature 컬럼을 가진 입력(행 순서가 곧 기록 순서).
        features: 순서가 확정된 숫자형 Feature.
        schema: 원본 Feature dtype을 포함한 Polars 스키마.
        path: 기록할 Memmap 파일.
        missing_policy: `reject`(결측 거부) 또는 `train_median`(NaN으로 보존, 무한대 거부).

    Returns:
        (행 수, Feature 수) 읽기·쓰기 Memmap.

    Raises:
        ValueError: 결측·무한대·정수 범위가 정책과 맞지 않거나, 읽는 중 행 수가 바뀌었다.
    """
    if missing_policy not in ("reject", "train_median"):
        raise ValueError("Unsupported training missing policy")
    selected = scan.select(features)
    count = selected.select(pl.len()).collect().item()
    if count < 2:
        raise ValueError("Training requires at least two rows")
    matrix = np.memmap(path, mode="w+", dtype=np.float64, shape=(count, len(features)))
    at = 0
    frames = selected.collect_batches(chunk_size=_BATCH_ROWS, maintain_order=True)
    try:
        for frame in frames:
            n = frame.height
            if at + n > count:
                raise ValueError("Dataset changed during pinned training read")
            if missing_policy == "reject" and any(frame.null_count().row(0)):
                raise ValueError("Training features contain null values")
            for name in features:
                if schema[name] in INTEGER_DTYPES:
                    values = frame[name]
                    maximum, minimum = values.max(), values.min()
                    # 전부 null인 Batch는 극값이 None이다. 결측 정책은 전처리 단계에서 판단한다.
                    if (
                        (maximum is not None and maximum > 2**53)
                        or (minimum is not None and minimum < -(2**53))
                    ):
                        raise ValueError("Integer feature exceeds the exact float64 range")
            values = np.asarray(frame.to_numpy(), dtype=np.float64)
            if missing_policy == "reject" and not np.isfinite(values).all():
                raise ValueError("Training features must be finite numeric values")
            if missing_policy == "train_median" and np.isinf(values).any():
                raise ValueError("Training features contain infinity")
            matrix[at:at + n] = values
            at += n
    finally:
        close = getattr(frames, "close", None)
        if close is not None:
            close()
    if at != count:
        raise ValueError("Dataset changed during pinned training read")
    matrix.flush()
    return matrix


def fit_staged_medians(matrix: np.ndarray, features: list[str]) -> FittedIFMedianImputer:
    """열마다 독립 중앙값을 Fit하고 전체 2D 배열 사본 생성을 피한다."""
    medians = []
    for col in range(matrix.shape[1]):
        column = matrix[:, col]
        if np.isnan(column).all():
            raise ValueError("Entirely missing training features require explicit exclusion")
        medians.append(_train_median(column))
    return FittedIFMedianImputer(tuple(features), tuple(medians))


def staged_model_input(
    matrix: np.ndarray, preprocessing: FittedIFMedianImputer, *, stage: Path,
) -> np.memmap:
    """Fit 상태를 고정하고 Chunk마다 결측을 치환한 Model 입력을 디스크에 기록한다."""
    output = np.memmap(
        stage / "transformed.float64", mode="w+", dtype=np.float64, shape=matrix.shape
    )
    for start in range(0, matrix.shape[0], _BATCH_ROWS):
        end = min(start + _BATCH_ROWS, matrix.shape[0])
        output[start:end] = preprocessing.transform(
            matrix[start:end], feature_names=preprocessing.feature_names
        )
    output.flush()
    return output
