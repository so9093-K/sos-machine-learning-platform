"""불변 Model Revision의 신뢰된 Worker 복원 계약.

joblib은 코드 실행이 가능한 직렬화 형식이다. API·사용자 제공 파일에서는
호출하지 않고, SOS가 게시한 모델만 전용 Worker 프로세스에서 복원한다.
"""

import platform
from pathlib import Path
from tempfile import TemporaryDirectory

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from .ml_isolation_forest import IsolationForestConfig, TrainedIsolationForest
from .object_store import ObjectStore


def assert_model_runtime_compatible(revision: dict) -> None:
    """지원된 알고리즘·직렬화 형식 및 정확한 실행 버전을 확인한다."""
    runtime = revision["runtime"]
    if revision["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model algorithm")
    if runtime.get("model_format") != "joblib" or runtime.get("loading") != "trusted_worker_only":
        raise ValueError("Unsupported model serialization contract")
    required = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
    }
    if any(runtime.get(name) != value for name, value in required.items()):
        raise ValueError("Model Runtime is incompatible with the current Worker")
    if not isinstance(runtime.get("feature_dtypes"), list):
        raise ValueError("Model feature signature is unavailable")
    if len(runtime["feature_dtypes"]) != len(revision["features"]):
        raise ValueError("Model feature signature is invalid")


def load_trusted_isolation_forest(revision: dict, scratch: Path) -> TrainedIsolationForest:
    """SOS 모델 게시 기록이 가리키는 아티팩트만 검증해 복원한다.

    Args:
        revision: PostgreSQL의 불변 Model Revision.
        scratch: 실행 Attempt 소유의 임시 디렉터리.

    Returns:
        TrainedIsolationForest: 점수 방향이 표준화된 모델 어댑터.

    Raises:
        ValueError: 신뢰·무결성·호환성·모델 서명 검증 실패.
    """
    assert_model_runtime_compatible(revision)
    storage = ObjectStore()
    expected_key = f"results/{revision['job_id']}/{revision['attempt_id']}.model.joblib"
    if revision["location"] != storage.location(expected_key):
        raise ValueError("Model artifact is outside its immutable Attempt location")
    digest = revision["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Model artifact digest is invalid")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError("Model artifact digest is invalid") from exc
    with TemporaryDirectory(prefix="ml-model-", dir=scratch) as directory:
        path = Path(directory) / "model.joblib"
        storage.get(expected_key, path, expected_sha256=digest)
        # 바이트 검증 뒤에도 joblib은 신뢰된 내부 결과에 대해서만 허용한다.
        estimator = joblib.load(path)
    if type(estimator) is not IsolationForest:
        raise ValueError("Model artifact has an unsupported estimator type")
    n_features = len(revision["features"])
    if estimator.n_features_in_ != n_features:
        raise ValueError("Model artifact feature signature does not match the revision")
    parameters = IsolationForestConfig(**revision["params"])
    if estimator.n_estimators != parameters.n_estimators:
        raise ValueError("Model artifact parameters do not match the revision")
    return TrainedIsolationForest(estimator, parameters, n_features)
