"""Isolation Forest 기준 방법의 Train 기반 숫자형 전처리.

Dataset 역할·시간 분할·Job/Artifact 생명주기는 이 모듈의 책임이 아니다.
학습된 전처리 상태는 이후 신뢰된 Model Revision 아티팩트에 포함할 수 있다.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def _numeric_matrix(data: object, *, n_features: int) -> np.ndarray:
    """결측 NaN만 허용하는 2차원 숫자 입력을 검사하고 복사한다."""
    if np.ma.isMaskedArray(data) and np.ma.is_masked(data):
        raise ValueError("Masked arrays must explicitly represent missing values as NaN")
    try:
        raw = np.asarray(data)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Features must be a regular numeric matrix") from exc
    if raw.ndim != 2 or raw.shape[0] < 1 or raw.shape[1] != n_features:
        raise ValueError("Feature matrix must be nonempty and match the input signature")
    if raw.dtype.kind not in "fiu":
        raise ValueError("Features must be numeric; strings, booleans and objects are unsupported")
    if raw.dtype.kind in "iu" and (raw.max() > 2**53 or raw.min() < -(2**53)):
        raise ValueError("Integer features exceed exact float64 range")
    # 혼합 Python 리스트는 np.asarray 이전의 큰 정수를 float로 반올림할 수 있다.
    # ndarray의 변환 이전 정밀도는 호출 측 입력 서명에서 보장해야 한다.
    if raw.dtype.kind == "f" and isinstance(data, (list, tuple)):
        for row in data:
            for value in row:
                if isinstance(value, (bool, np.bool_)):
                    raise ValueError("Boolean features are not supported")
                if isinstance(value, (int, np.integer)) and abs(int(value)) > 2**53:
                    raise ValueError("Integer features exceed exact float64 range")
    with np.errstate(over="ignore", invalid="ignore"):
        matrix = np.asarray(raw, dtype=np.float64)
    if not np.isfinite(matrix[~np.isnan(matrix)]).all():
        raise ValueError("Infinite or overflowed feature values are not supported")
    return matrix.copy()


def _feature_names(names: Sequence[str]) -> tuple[str, ...]:
    """Feature 서명이 이름 하나로 잘못 분해되거나 중복되지 않게 검증한다."""
    if isinstance(names, (str, bytes)):
        raise ValueError("Feature names must be an ordered sequence of column names")
    try:
        values = tuple(names)
    except TypeError as exc:
        raise ValueError("Feature names must be an ordered sequence") from exc
    if not values or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("Nonempty feature names are required")
    if len(set(values)) != len(values):
        raise ValueError("Feature names must be unique")
    return values


def _train_median(values: np.ndarray) -> float:
    """큰 유한값의 짝수행 중앙값 계산에서 합계 오버플로를 피한다."""
    with np.errstate(over="ignore"):
        median = float(np.nanmedian(values))
    if math.isfinite(median):
        return median
    finite = values[~np.isnan(values)]
    left = (len(finite) - 1) // 2
    right = len(finite) // 2
    ordered = np.partition(finite, (left, right))
    median = float(ordered[left] / 2.0 + ordered[right] / 2.0)
    if not math.isfinite(median):
        raise ValueError("Training median must be finite")
    return median


@dataclass(frozen=True)
class FittedIFMedianImputer:
    """Train에서 학습한 결측 대체 기준을 새 입력에 그대로 적용한다.

    NaN은 모든 Feature에서 허용하며 Train에서 계산한 중앙값으로 대체한다.
    입력 Feature 순서·개수는 학습 시 서명과 일치해야 한다. 모델 선정 및
    데이터별 Feature 제외 규칙은 별도 단계에서 결정한다.

    Attributes:
        feature_names: 학습한 Feature의 순서가 유지된 입력 서명.
        medians: 각 Feature의 Train 중앙값.
    """

    feature_names: tuple[str, ...]
    medians: tuple[float, ...]

    def __post_init__(self) -> None:
        names = _feature_names(self.feature_names)
        if not isinstance(self.feature_names, tuple) or not isinstance(self.medians, tuple):
            raise ValueError("Fitted preprocessing state must contain immutable tuples")
        if len(self.medians) != len(names) or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not math.isfinite(value)
            for value in self.medians
        ):
            raise ValueError("Fitted median state must match features and be finite")

    def transform(self, data: object, *, feature_names: Sequence[str]) -> np.ndarray:
        """학습 기준을 바꾸지 않고 숫자형 행렬을 변환한다.

        Args:
            data: 행이 관측치인 2차원 입력. NaN은 결측을 나타낸다.
            feature_names: 입력 행렬의 순서가 확정된 Feature 이름.

        Returns:
            np.ndarray: 결측이 중앙값으로 대체된 float64 행렬.

        Raises:
            ValueError: Feature 서명·입력 크기·숫자값이 유효하지 않은 경우.
        """
        if _feature_names(feature_names) != self.feature_names:
            raise ValueError("Inference feature signature differs from training")
        matrix = _numeric_matrix(data, n_features=len(self.feature_names))
        missing = np.isnan(matrix)
        if missing.any():
            reference = np.asarray(self.medians, dtype=np.float64)
            matrix[missing] = np.broadcast_to(reference, matrix.shape)[missing]
        return matrix


def fit_if_median_imputer(
    training: object, *, feature_names: Sequence[str]
) -> FittedIFMedianImputer:
    """Train 행에만 Fit하며 외부 구간의 결측·분포를 참조하지 않는다.

    Args:
        training: 학습 관측치의 2차원 숫자형 행렬.
        feature_names: 사용자가 확정한 순서 있는 Feature 이름.

    Returns:
        FittedIFMedianImputer: 다른 구간에도 재사용 가능한 전처리 상태.

    Raises:
        ValueError: 비어 있거나 중복된 Feature, 전체 결측 컬럼 또는 잘못된 입력.
    """
    names = _feature_names(feature_names)
    matrix = _numeric_matrix(training, n_features=len(names))
    if matrix.shape[0] < 2:
        raise ValueError("At least two training observations are required")
    if np.isnan(matrix).all(axis=0).any():
        raise ValueError("Entirely missing training features require explicit exclusion")
    medians = tuple(_train_median(matrix[:, j]) for j in range(matrix.shape[1]))
    return FittedIFMedianImputer(feature_names=names, medians=medians)
