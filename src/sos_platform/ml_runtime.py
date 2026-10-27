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

from .errors import PlatformError
from .ml_if_preprocessing import FittedIFMedianImputer
from .ml_isolation_forest import IsolationForestConfig, TrainedIsolationForest

IF_BUNDLE_SCHEMA = "if_model_with_preprocessing_v1"
from .object_store import ObjectStore


def _python_minor(version: object) -> tuple[str, str] | None:
    parts = version.split(".") if isinstance(version, str) else []
    return (parts[0], parts[1]) if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]) else None


def assert_model_runtime_compatible(revision: dict) -> None:
    """지원된 알고리즘·직렬화 형식과 모델 복원에 영향을 주는 실행 버전을 확인한다.

    joblib 복원 호환성은 numpy·scikit-learn 버전이 좌우하므로 두 라이브러리는 정확히
    일치해야 한다. Python은 같은 minor 버전이면 허용한다. 패치 릴리스는 pickle 형식과
    객체 구조를 바꾸지 않으므로, 베이스 이미지의 보안 패치만으로 기존 모델을 사용
    불가로 만들지 않는다.

    Raises:
        ValueError: 지원하지 않는 알고리즘·직렬화 계약 또는 손상된 모델 서명.
        PlatformError: 모델을 학습한 실행 버전이 현재 Worker와 호환되지 않음.
    """
    runtime = revision["runtime"]
    if revision["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model algorithm")
    if runtime.get("model_format") != "joblib" or runtime.get("loading") != "trusted_worker_only":
        raise ValueError("Unsupported model serialization contract")
    if runtime.get("artifact_schema") not in (None, IF_BUNDLE_SCHEMA):
        raise ValueError("Unknown model artifact schema")
    if runtime.get("artifact_schema") == IF_BUNDLE_SCHEMA:
        if runtime.get("preprocessing_mode") != "train_median_v1":
            raise ValueError("Unknown model preprocessing contract")
    if (
        _python_minor(runtime.get("python")) != tuple(platform.python_version_tuple()[:2])
        or runtime.get("numpy") != np.__version__
        or runtime.get("scikit_learn") != sklearn.__version__
    ):
        raise PlatformError("SOS-ML-MODEL-RUNTIME-INCOMPATIBLE")
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
    if revision.get("lifecycle_state", "Active") != "Active":
        raise ValueError("Deleted or deleting model artifact cannot be loaded")
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
        artifact = joblib.load(path)
    preprocessing = None
    if runtime := revision["runtime"]:
        if runtime.get("artifact_schema") == IF_BUNDLE_SCHEMA:
            if (
                type(artifact) is not dict
                or set(artifact) != {"schema", "estimator", "preprocessing"}
                or artifact["schema"] != IF_BUNDLE_SCHEMA
                or type(artifact["preprocessing"]) is not FittedIFMedianImputer
            ):
                raise ValueError("Model artifact bundle does not match revision schema")
            preprocessing = artifact["preprocessing"]
            # deserialization은 __post_init__을 보장하지 않으므로 복원 후에도 검증한다.
            preprocessing.__post_init__()
            if preprocessing.feature_names != tuple(revision["features"]):
                raise ValueError("Model preprocessing feature signature is invalid")
            reference = runtime.get("feature_reference_medians")
            if not isinstance(reference, list) or tuple(reference) != preprocessing.medians:
                raise ValueError("Model preprocessing reference disagrees with revision")
            estimator = artifact["estimator"]
        else:
            estimator = artifact
    if type(estimator) is not IsolationForest:
        raise ValueError("Model artifact has an unsupported estimator type")
    n_features = len(revision["features"])
    if estimator.n_features_in_ != n_features:
        raise ValueError("Model artifact feature signature does not match the revision")
    parameters = IsolationForestConfig(**revision["params"])
    if estimator.n_estimators != parameters.n_estimators:
        raise ValueError("Model artifact parameters do not match the revision")
    return TrainedIsolationForest(estimator, parameters, n_features, preprocessing=preprocessing)
