"""Isolation Forest 기준 방법의 Train 기반 숫자형 전처리.

Dataset 역할·시간 분할·Job/Artifact 생명주기는 이 모듈의 책임이 아니다.
학습된 전처리 상태는 이후 신뢰된 Model Revision 아티팩트에 포함할 수 있다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def _numeric_matrix(data: object, *, n_features: int) -> np.ndarray:
    """결측 NaN만 허용하는 2차원 숫자 입력을 검사하고 복사한다."""
    raw = np.asarray(data)
    if raw.dtype.kind == "b":
        raise ValueError("Boolean features are not supported")
    if raw.dtype.kind in "iu" and raw.size and (raw.max() > 2**53 or raw.min() < -(2**53)):
        raise ValueError("Integer features exceed exact float64 range")
    try:
        matrix = np.asarray(data, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Features must be numeric") from exc
    if matrix.ndim != 2 or matrix.shape[0] < 1 or matrix.shape[1] != n_features:
        raise ValueError("Feature matrix must be nonempty and match the input signature")
    if np.isinf(matrix).any():
        raise ValueError("Infinite feature values are not supported")
    return matrix.copy()


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
        if tuple(feature_names) != self.feature_names:
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
    names = tuple(feature_names)
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Nonempty feature names are required")
    if len(set(names)) != len(names):
        raise ValueError("Feature names must be unique")
    matrix = _numeric_matrix(training, n_features=len(names))
    if matrix.shape[0] < 2:
        raise ValueError("At least two training observations are required")
    if np.isnan(matrix).all(axis=0).any():
        raise ValueError("Entirely missing training features require explicit exclusion")
    medians = tuple(float(np.nanmedian(matrix[:, j])) for j in range(matrix.shape[1]))
    return FittedIFMedianImputer(feature_names=names, medians=medians)
