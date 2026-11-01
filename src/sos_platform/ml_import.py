"""MLflow에 등록된 고정 모델 버전을 SOS Model Revision으로 연결한다. Worker 자식 프로세스에서 실행한다.

생산 Pipeline이 학습·평가·등록한 모델 버전을 다시 학습하지 않는다. 모델 패키지 계약
(`ml_model_package`)을 확인하고 SOS Runtime에서 같은 점수가 나는지 검증한 뒤, 아티팩트를 이
Attempt의 SOS 저장 위치로 옮겨 게시한다. 이후 제공·Scoring·온라인 추론은 SOS가 학습한 판본과 같은
복원 경로를 쓴다.
"""

import hashlib
import io
from pathlib import Path

from .errors import PlatformError
from .ml_model_package import (
    ESTIMATOR_FILE,
    MANIFEST_FILE,
    MAX_ESTIMATOR_BYTES,
    MAX_MANIFEST_BYTES,
    read_manifest,
    restore_package,
)
from .ml_outputs import output_contract_for_training
from .ml_runtime import (
    IF_SKOPS_SCHEMA,
    PREPROCESSING_MODE,
    SAFE_LOADING,
    assert_model_runtime_compatible,
)
from .mlflow_config import MLflowSettings
from .mlflow_errors import MLflowDeliveryError
from .mlflow_tracking import MLflowClient
from .model_artifacts import SKOPS


def _ready_version(client: MLflowClient, name: str, version: str) -> dict:
    entry = client.get("model-versions/get", {"name": name, "version": version}).get(
        "model_version"
    )
    if not isinstance(entry, dict) or entry.get("name") != name or entry.get("version") != version:
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    if entry.get("status") != "READY":
        raise PlatformError("SOS-ML-IMPORT-SOURCE-UNAVAILABLE")
    return entry


def import_registered_model(spec: dict, artifact: Path) -> dict:
    """MLflow 모델 버전의 패키지를 확인하고 게시할 Model Revision 기록을 만든다.

    Args:
        spec: 연결 Job 사양(`mlflow_model`, `mlflow_version`, `registry_key`).
        artifact: 이 Attempt가 소유한 `estimator.skops` 임시 경로. 부모가 SOS 저장소로 올린다.

    Returns:
        Model Revision 게시 기록. `source`에 MLflow 출처와 생산 측 학습 근거를 담는다.

    Raises:
        PlatformError: MLflow 모델 버전을 확인할 수 없거나(SOS-ML-IMPORT-SOURCE-UNAVAILABLE),
            패키지가 계약과 다르거나(SOS-ML-IMPORT-INVALID), 생산 Runtime이 SOS와 다르다.
    """
    settings = MLflowSettings.from_env()
    if settings is None:
        raise PlatformError("SOS-ML-IMPORT-SOURCE-UNAVAILABLE")
    client = MLflowClient(settings)
    name, version = spec["mlflow_model"], spec["mlflow_version"]
    try:
        entry = _ready_version(client, name, version)
        raw = io.BytesIO()
        client.model_version_artifact(name, version, MANIFEST_FILE, raw, limit=MAX_MANIFEST_BYTES)
        with artifact.open("wb") as out:
            client.model_version_artifact(
                name, version, ESTIMATOR_FILE, out, limit=MAX_ESTIMATOR_BYTES
            )
    except MLflowDeliveryError as exc:
        raise PlatformError("SOS-ML-IMPORT-SOURCE-UNAVAILABLE") from exc
    try:
        manifest = read_manifest(raw.getvalue())
        produced = manifest["runtime"]
        revision = {
            "algorithm": manifest["algorithm"],
            "features": manifest["features"],
            "runtime": {
                "model_format": SKOPS,
                "artifact_schema": IF_SKOPS_SCHEMA,
                "loading": SAFE_LOADING,
                "output": output_contract_for_training(manifest["algorithm"]).record(),
                "feature_dtypes": manifest["feature_dtypes"],
                "feature_reference_medians": manifest["preprocessing"]["medians"],
                "feature_training_profile": manifest["feature_training_profile"],
                "preprocessing_mode": PREPROCESSING_MODE,
                "python": produced["python"],
                "numpy": produced["numpy"],
                "scikit_learn": produced["scikit_learn"],
                "skops": produced["skops"],
            },
        }
        # 생산 Runtime이 다르면 패키지를 복원하기 전에 판본 호환성 오류로 끝낸다.
        assert_model_runtime_compatible(revision)
        restore_package(manifest, artifact)
    except ValueError as exc:
        raise PlatformError("SOS-ML-IMPORT-INVALID") from exc
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            digest.update(chunk)
    return {
        **revision,
        "dataset_version": None,
        "params": manifest["params"],
        "training_score_summary": manifest["training_score_summary"],
        "source": {
            "mlflow_model": name,
            "mlflow_version": version,
            "run_id": entry.get("run_id") if isinstance(entry.get("run_id"), str) else None,
            "source_uri": entry.get("source") if isinstance(entry.get("source"), str) else None,
            "estimator_sha256": digest.hexdigest(),
            "manifest_sha256": hashlib.sha256(raw.getvalue()).hexdigest(),
            "training": manifest["training"],
        },
    }
