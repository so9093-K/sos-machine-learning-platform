"""scikit-learn Isolation Forest 실행 어댑터.

훈련 알고리즘과 SOS의 Job·결과 게시·정책 상태를 분리한다.
모델 객체의 저장·역직렬화는 신뢰 경계를 갖는 상위 Runtime의 책임이다.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

from . import ml_if_config
from .ml_anomaly import normalize_isolation_forest_scores
from .ml_if_preprocessing import FittedIFMedianImputer


def _matrix(data: object, *, n_features: int | None = None) -> np.ndarray:
    """숫자 행렬의 크기·결측·비유한값을 명시적으로 검증한다."""
    try:
        matrix = np.asarray(data, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Features must contain finite numeric values") from exc
    if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1]:
        raise ValueError("Features must be a nonempty 2-D numeric matrix")
    if n_features is not None and matrix.shape[1] != n_features:
        raise ValueError("Inference feature count does not match training")
    if not np.isfinite(matrix).all():
        raise ValueError("Features must contain finite numeric values")
    return matrix


@dataclass(frozen=True)
class TrainedIsolationForest:
    """점수는 항상 높을수록 이상이며, 확률값이 아니다."""

    estimator: IsolationForest
    config: ml_if_config.IsolationForestConfig
    n_features: int
    preprocessing: FittedIFMedianImputer | None = None

    def score_anomalies(self, features: object) -> tuple[float, ...]:
        """신뢰된 모델 판본에 연결된 전처리를 적용한 뒤 이상 점수를 산출한다."""
        if self.preprocessing is None:
            # 과거 모델 판본의 숫자형 완전 입력 계약을 그대로 유지한다.
            matrix = _matrix(features, n_features=self.n_features)
        else:
            matrix = self.preprocessing.transform(
                features, feature_names=self.preprocessing.feature_names
            )
        return normalize_isolation_forest_scores(self.estimator.score_samples(matrix).tolist())


def train_isolation_forest(
    features: object, config: ml_if_config.IsolationForestConfig | None = None
) -> TrainedIsolationForest:
    """검증된 학습 행렬에 고정된 설정으로 Isolation Forest를 학습한다."""
    settings = config or ml_if_config.IsolationForestConfig()
    matrix = _matrix(features)
    if matrix.shape[0] < 2:
        raise ValueError("At least two training rows are required")
    if isinstance(settings.max_samples, int) and settings.max_samples > matrix.shape[0]:
        raise ValueError("max_samples exceeds the number of training rows")
    if isinstance(settings.max_features, int) and settings.max_features > matrix.shape[1]:
        raise ValueError("max_features exceeds the number of feature columns")
    model = IsolationForest(
        n_estimators=settings.n_estimators,
        max_samples=settings.max_samples,
        max_features=settings.max_features,
        bootstrap=settings.bootstrap,
        random_state=settings.random_state,
        contamination="auto",
        n_jobs=1,
    )
    model.fit(matrix)
    return TrainedIsolationForest(model, settings, matrix.shape[1])
