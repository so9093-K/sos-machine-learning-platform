"""불변 Model Revision의 복원 계약.

판본의 출처에 따라 복원 방식이 다르다. SOS Worker가 학습해 직접 쓴 joblib 묶음은 신뢰된 SOS 실행
환경(Worker 자식 프로세스·온라인 서빙 복제본)에서만 복원한다. joblib은 코드 실행이 가능한 형식이기
때문이다. MLflow에서 연결한 판본은 허용한 타입만 복원하는 skops 파일로만 받아, 모델 관리 권한이
SOS 실행 환경에서의 코드 실행 권한이 되지 않게 한다.
"""

import platform
from pathlib import Path
from tempfile import TemporaryDirectory

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from .errors import PlatformError
from .ml_if_config import IsolationForestConfig
from .ml_if_preprocessing import FittedIFMedianImputer
from .ml_isolation_forest import TrainedIsolationForest
from .model_artifacts import JOBLIB, SKOPS, revision_artifact_key
from .object_store import ObjectStore

IF_BUNDLE_SCHEMA = "if_model_with_preprocessing_v1"
# MLflow에서 연결한 판본: skops IsolationForest와 판본 기록의 학습 중앙값 전처리.
IF_SKOPS_SCHEMA = "if_estimator_skops_v1"
# 판본에 기록되는 복원 계약 식별자. 이름과 달리 Worker만이 아니라 신뢰된 SOS 실행 환경 전체를 뜻한다.
# 기존 판본의 복원 호환을 위해 값을 바꾸지 않는다.
TRUSTED_LOADING = "trusted_worker_only"
SAFE_LOADING = "skops_allowlist"
# IsolationForest 복원에 필요한 skops 비기본 타입. 이 밖의 타입이 있으면 복원하지 않는다.
SKOPS_TRUSTED_TYPES = ("sklearn.tree._tree.Tree",)
PREPROCESSING_MODE = "train_median_v1"


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
        PlatformError: 모델을 학습한 실행 버전이 현재 실행 Runtime과 호환되지 않음.
    """
    runtime = revision["runtime"]
    if revision["algorithm"] != "isolation_forest":
        raise ValueError("Unsupported model algorithm")
    contract = (runtime.get("model_format"), runtime.get("loading"))
    if contract == (JOBLIB, TRUSTED_LOADING):
        if runtime.get("artifact_schema") not in (None, IF_BUNDLE_SCHEMA):
            raise ValueError("Unknown model artifact schema")
        if (runtime.get("artifact_schema") == IF_BUNDLE_SCHEMA
                and runtime.get("preprocessing_mode") != PREPROCESSING_MODE):
            raise ValueError("Unknown model preprocessing contract")
    elif contract == (SKOPS, SAFE_LOADING):
        if (runtime.get("artifact_schema") != IF_SKOPS_SCHEMA
                or runtime.get("preprocessing_mode") != PREPROCESSING_MODE):
            raise ValueError("Unknown model artifact schema")
    else:
        raise ValueError("Unsupported model serialization contract")
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


def load_skops_estimator(path: Path) -> IsolationForest:
    """허용한 타입만 있는 skops 파일에서 IsolationForest를 복원한다.

    Raises:
        ValueError: 허용하지 않은 타입이 있거나 최상위 객체가 IsolationForest가 아니다.
    """
    import skops.io as sio

    if set(sio.get_untrusted_types(file=path)) - set(SKOPS_TRUSTED_TYPES):
        raise ValueError("Model artifact contains types outside the allowed list")
    estimator = sio.load(path, trusted=list(SKOPS_TRUSTED_TYPES))
    if type(estimator) is not IsolationForest:
        raise ValueError("Model artifact has an unsupported estimator type")
    return estimator


def load_model_revision(revision: dict, scratch: Path) -> TrainedIsolationForest:
    """SOS 모델 게시 기록이 가리키는 아티팩트만 검증해 복원한다.

    Args:
        revision: PostgreSQL의 불변 Model Revision.
        scratch: 호출자가 소유한 임시 디렉터리(Worker Attempt·서빙 복제본).

    Returns:
        TrainedIsolationForest: 점수 방향이 표준화된 모델 어댑터.

    Raises:
        ValueError: 신뢰·무결성·호환성·모델 서명 검증 실패.
    """
    if revision.get("lifecycle_state", "Active") != "Active":
        raise ValueError("Deleted or deleting model artifact cannot be loaded")
    assert_model_runtime_compatible(revision)
    storage = ObjectStore()
    expected_key = revision_artifact_key(revision)
    if revision["location"] != storage.location(expected_key):
        raise ValueError("Model artifact is outside its immutable Attempt location")
    digest = revision["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Model artifact digest is invalid")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError("Model artifact digest is invalid") from exc
    runtime = revision["runtime"]
    with TemporaryDirectory(prefix="ml-model-", dir=scratch) as directory:
        path = Path(directory) / Path(expected_key).name
        storage.get(expected_key, path, expected_sha256=digest)
        if runtime["model_format"] == SKOPS:
            estimator = load_skops_estimator(path)
        else:
            # 바이트 검증 뒤에도 joblib은 SOS Worker가 쓴 내부 결과에 대해서만 허용한다.
            artifact = joblib.load(path)
    preprocessing = None
    if runtime["model_format"] == SKOPS:
        # 전처리는 역직렬화하지 않고 판본 기록의 Feature 순서·학습 중앙값으로 다시 구성한다.
        reference = runtime.get("feature_reference_medians")
        if not isinstance(reference, list):
            raise ValueError("Model preprocessing reference is unavailable")
        preprocessing = FittedIFMedianImputer(tuple(revision["features"]), tuple(reference))
    else:
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
