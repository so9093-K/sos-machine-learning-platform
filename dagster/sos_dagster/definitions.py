"""활동 이상 탐지 모델 생산 Pipeline. Dagster가 학습·평가·MLflow 등록을 직접 실행한다.

SOS DB·저장소·Job에 접근하지 않는다. 생산한 모델은 MLflow 모델 버전으로 등록하고, 연결 설정이
있으면 SOS API에 그 고정 버전의 연결을 요청한다. 전처리·학습·학습 근거 계산과 모델 패키지는 SOS와
같은 라이브러리 함수를 쓰므로, SOS에서 같은 의미로 표시되고 같은 점수를 낸다.
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import polars as pl
from pydantic import Field

from dagster import (
    AssetMaterialization,
    Config,
    Definitions,
    Failure,
    MetadataValue,
    OpExecutionContext,
    in_process_executor,
    job,
    op,
)
from sos_platform.analysis_topics import ACTIVITY_FEATURES
from sos_platform.ml_anomaly import evaluate_anomaly_scores, quantile_threshold
from sos_platform.ml_if_config import IsolationForestConfig
from sos_platform.ml_if_preprocessing import fit_if_median_imputer
from sos_platform.ml_input import NUMERIC
from sos_platform.ml_isolation_forest import TrainedIsolationForest, train_isolation_forest
from sos_platform.ml_model_package import ESTIMATOR_FILE, MANIFEST_FILE, package_isolation_forest
from sos_platform.ml_temporal_split import select_temporal_partition, utc_observation_time
from sos_platform.ml_training_evidence import feature_training_profile, training_score_summary
from sos_platform.mlflow_config import MLflowSettings
from sos_platform.mlflow_errors import MLflowDeliveryError
from sos_platform.mlflow_tracking import MLflowClient

PRODUCER = "dagster:activity_anomaly_production"
TIMESTAMP = "ts"
EXPERIMENT = "activity-anomaly-production"


class ProductionConfig(Config):
    data_path: str = Field(description="CSV·Parquet 경로. ts(UTC 오프셋 포함)와 활동 Feature 44개가 필요하다.")
    registered_model: str = Field(default="activity-anomaly-if", description="등록할 MLflow 모델 이름")
    train_fraction: float = Field(default=0.6, gt=0, lt=1, description="시간순 Train 구간 비율")
    calibration_fraction: float = Field(default=0.2, gt=0, lt=1, description="Train 다음 Calibration 구간 비율")
    alert_fraction: float = Field(default=0.005, gt=0, lt=1, description="Calibration 점수로 정하는 판정 비율")
    n_estimators: int = Field(default=100, ge=1, le=2000)
    random_state: int = Field(default=42, ge=0, le=2**32 - 1)
    connect_to_sos: bool = Field(default=True, description="MLflow 등록 후 SOS에 고정 버전을 연결한다. 공급 CLI로 연결할 예정이면 false로 지정한다")
    registry_key: str = Field(default="default", description="연결한 판본을 관리할 SOS 모델 그룹")


def _source(path: str) -> pl.LazyFrame:
    suffix = Path(path).suffix.lower()
    if suffix == ".parquet":
        return pl.scan_parquet(path)
    if suffix == ".csv":
        return pl.scan_csv(path)
    raise Failure("data_path는 .csv 또는 .parquet이어야 합니다.")


def _file_sha256(path: str) -> str | None:
    file = Path(path)
    if not file.is_file():
        return None
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _partition(source: pl.LazyFrame, bounds: dict, partition: str) -> tuple[np.ndarray, dict]:
    frame, evidence = select_temporal_partition(
        source, timestamp_column=TIMESTAMP, train_end=bounds["train_end"],
        calibration_end=bounds["calibration_end"], partition=partition,
    )
    values = frame.select(ACTIVITY_FEATURES).collect().to_numpy().astype(np.float64)
    if np.isinf(values).any():
        raise Failure("활동 Feature에 무한값이 있습니다.")
    return values, evidence


@op(description="시간순 Train·Calibration·Test로 나누어 Isolation Forest를 학습하고, Calibration 점수로 판정 기준을 정해 Test 구간을 평가합니다. 결과를 SOS 모델 패키지로 만듭니다.")
def train_and_evaluate(context: OpExecutionContext, config: ProductionConfig) -> dict:
    if config.train_fraction + config.calibration_fraction >= 1:
        raise Failure("train_fraction + calibration_fraction은 1보다 작아야 합니다.")
    source = _source(config.data_path)
    schema = source.collect_schema()
    missing = [name for name in (TIMESTAMP, *ACTIVITY_FEATURES) if name not in schema]
    if missing:
        raise Failure("필수 컬럼 없음: " + ", ".join(missing[:12]))
    if any(schema[name] not in NUMERIC for name in ACTIVITY_FEATURES):
        raise Failure("활동 Feature는 숫자형이어야 합니다.")
    event = utc_observation_time(source, TIMESTAMP).dt.epoch("ms")
    stats = source.select(
        event.quantile(config.train_fraction, interpolation="nearest").alias("train"),
        event.quantile(config.train_fraction + config.calibration_fraction,
                       interpolation="nearest").alias("calibration"),
    ).collect().row(0, named=True)
    if stats["train"] is None or stats["calibration"] is None:
        raise Failure("ts에서 시간 분할 경계를 정할 수 없습니다.")
    # 경계 시각의 행은 앞 구간에 둔다(경계는 다음 구간의 시작 시각).
    bounds = {
        "train_end": datetime.fromtimestamp(stats["train"] / 1000, tz=UTC) + timedelta(milliseconds=1),
        "calibration_end": datetime.fromtimestamp(stats["calibration"] / 1000, tz=UTC)
        + timedelta(milliseconds=1),
    }
    try:
        train, split = _partition(source, bounds, "train")
        calibration, _ = _partition(source, bounds, "calibration")
        test, _ = _partition(source, bounds, "test")
    except ValueError as exc:
        raise Failure(f"시간 분할 실패: {exc}") from exc

    features = list(ACTIVITY_FEATURES)
    config_params = IsolationForestConfig(
        n_estimators=config.n_estimators, random_state=config.random_state,
    )
    preprocessing = fit_if_median_imputer(train, feature_names=features)
    transformed = preprocessing.transform(train, feature_names=features)
    trained = train_isolation_forest(transformed, config_params)
    summary, _ = training_score_summary(trained, transformed)
    profile = feature_training_profile(transformed, features)
    model = TrainedIsolationForest(
        trained.estimator, config_params, len(features), preprocessing=preprocessing,
    )
    threshold = quantile_threshold(model.score_anomalies(calibration), config.alert_fraction)
    test_evaluation = evaluate_anomaly_scores(model.score_anomalies(test), threshold=threshold)
    rows = {key: split[f"{key}_rows"] for key in ("train", "calibration", "test")}
    files = package_isolation_forest(
        model,
        feature_dtypes=[str(schema[name]) for name in features],
        training_score_summary=summary,
        feature_training_profile=profile,
        training={
            "producer": PRODUCER,
            "data_reference": config.data_path,
            "data_sha256": _file_sha256(config.data_path),
            "rows": rows,
            "evaluation": {
                "temporal_split": {
                    "timestamp_column": TIMESTAMP,
                    "train_end": split["train_end"],
                    "calibration_end": split["calibration_end"],
                },
                "alert_fraction": config.alert_fraction,
                "calibration_threshold": threshold,
                "test": test_evaluation,
            },
        },
    )
    context.log.info(
        "학습 %d행 · Calibration %d행 · Test %d행 · Test 판정 비율 %.4f",
        rows["train"], rows["calibration"], rows["test"], test_evaluation["alert_rate"],
    )
    return {
        "registered_model": config.registered_model,
        "connect_to_sos": config.connect_to_sos,
        "registry_key": config.registry_key,
        "files": files,
        "params": {
            "n_estimators": config.n_estimators, "random_state": config.random_state,
            "train_fraction": config.train_fraction,
            "calibration_fraction": config.calibration_fraction,
            "alert_fraction": config.alert_fraction,
        },
        "metrics": {
            "training_score_mean": summary["score_mean"],
            "calibration_threshold": threshold,
            "test_alert_rate": test_evaluation["alert_rate"],
            "test_predicted_anomalies": test_evaluation["predicted_anomalies"],
        },
        "rows": rows,
    }


def _mlflow() -> MLflowClient:
    settings = MLflowSettings.from_env()
    if settings is None:
        raise Failure("SOS_MLFLOW_TRACKING_URI가 필요합니다.")
    return MLflowClient(settings)


def _experiment(client: MLflowClient) -> str:
    try:
        found = client.get("experiments/get-by-name", {"experiment_name": EXPERIMENT})
        return found["experiment"]["experiment_id"]
    except MLflowDeliveryError as exc:
        if not str(exc).startswith("SOS-MLFLOW-HTTP-404"):
            raise
    return client.post("experiments/create", {"name": EXPERIMENT})["experiment_id"]


def _ensure_registered_model(client: MLflowClient, name: str) -> None:
    try:
        client.get("registered-models/get", {"name": name})
    except MLflowDeliveryError as exc:
        if not str(exc).startswith("SOS-MLFLOW-HTTP-404"):
            raise
        client.post("registered-models/create", {"name": name})


@op(description="학습·평가 기록과 모델 패키지를 MLflow Run에 남기고, 그 패키지를 MLflow 모델 버전으로 등록합니다.")
def register_in_mlflow(context: OpExecutionContext, produced: dict) -> dict:
    client = _mlflow()
    started = int(time.time() * 1000)
    run = client.post("runs/create", {
        "experiment_id": _experiment(client),
        "run_name": f"activity-anomaly-{context.run_id[:8]}",
        "start_time": started,
        "tags": [
            {"key": "sos.producer", "value": PRODUCER},
            {"key": "dagster.run_id", "value": context.run_id},
        ],
    })["run"]["info"]
    run_id, artifact_uri = run["run_id"], run["artifact_uri"]
    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise Failure("MLflow 서버가 아티팩트를 프록시하도록(--artifacts-destination) 설정해야 합니다.")
    client.post("runs/log-batch", {
        "run_id": run_id,
        "params": [{"key": key, "value": str(value)} for key, value in produced["params"].items()],
        "metrics": [
            {"key": key, "value": float(value), "timestamp": started, "step": 0}
            for key, value in produced["metrics"].items()
        ],
    })
    for name in (MANIFEST_FILE, ESTIMATOR_FILE):
        client.put_artifact(f"{artifact_uri[len(prefix):]}/sos_model/{name}", produced["files"][name])
    client.post("runs/update", {"run_id": run_id, "status": "FINISHED",
                                "end_time": int(time.time() * 1000)})
    name = produced["registered_model"]
    _ensure_registered_model(client, name)
    version = client.post("model-versions/create", {
        "name": name, "source": artifact_uri + "/sos_model", "run_id": run_id,
        "description": "활동 이상 탐지 Isolation Forest. sos_model/ 아래 SOS 모델 패키지(sos_model.json·estimator.skops).",
    })["model_version"]["version"]
    for _ in range(60):
        entry = client.get("model-versions/get", {"name": name, "version": version})["model_version"]
        if entry.get("status") == "READY":
            break
        time.sleep(1)
    else:
        raise Failure("MLflow 모델 버전이 READY 상태가 되지 않았습니다.")
    context.log_event(AssetMaterialization(
        asset_key="activity_anomaly_model",
        metadata={
            "registered_model": name,
            "version": version,
            "run_id": run_id,
            "test_alert_rate": MetadataValue.float(float(produced["metrics"]["test_alert_rate"])),
        },
    ))
    return {
        "registered_model": name, "version": version, "run_id": run_id,
        "connect_to_sos": produced["connect_to_sos"], "registry_key": produced["registry_key"],
    }


def _sos(method: str, path: str, token: str, body: dict | None = None,
         key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    request = Request(
        os.environ["SOS_API_URL"].rstrip("/") + path, method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise Failure(f"SOS API {path} 실패: HTTP {exc.code} {exc.read()[:300].decode(errors='replace')}") from exc


@op(description="등록한 MLflow 모델 버전을 SOS에 연결하도록 요청하고, SOS가 패키지를 검증해 Model Revision을 게시할 때까지 기다립니다.")
def connect_to_sos(context: OpExecutionContext, registered: dict) -> None:
    label = f"{registered['registered_model']} v{registered['version']}"
    if not registered["connect_to_sos"]:
        context.log.info("SOS 연결을 요청하지 않았습니다. MLflow 모델 버전 %s", label)
        return
    token = os.getenv("SOS_SUPPLY_TOKEN")
    if not os.getenv("SOS_API_URL") or not token:
        raise Failure(
            "SOS 연결에는 SOS_API_URL과 SOS_SUPPLY_TOKEN(automation 서비스 계정)이 필요합니다. "
            f"MLflow 등록은 완료됐습니다: {label}"
        )
    item = _sos("POST", "/api/ml/models/imports", token, {
        "mlflow_model": registered["registered_model"],
        "mlflow_version": registered["version"],
        "registry_key": registered["registry_key"],
    }, key=f"dagster-{registered['run_id']}")
    try:
        while item["status"] in ("Queued", "Running"):
            time.sleep(2)
            item = _sos("GET", f"/api/ml/models/imports/{item['id']}", token)
    finally:
        # Dagster Run을 종료하면 대기 중인 SOS 연결도 취소를 요청한다. 실제 중단은 SOS가 확정한다.
        if item["status"] in ("Queued", "Running"):
            _sos("POST", f"/api/ml/models/imports/{item['id']}/cancel", token)
    if item["status"] != "Succeeded":
        raise Failure(f"SOS 모델 연결 {item['status']}: {item.get('error_code')} {item.get('error')}")
    context.log_event(AssetMaterialization(
        asset_key="sos_model_revision",
        metadata={"model_revision_id": item["result"]["model_revision_id"],
                  "import_job_id": item["id"], "mlflow_version": registered["version"]},
    ))


@job(
    executor_def=in_process_executor,
    description=(
        "활동 이상 탐지 모델 생산. 입력 파일로 학습·평가하고 MLflow 모델 버전으로 등록한 뒤 SOS에 연결한다. "
        "Run마다 새 MLflow Run·모델 버전을 만든다."
    ),
)
def activity_anomaly_production():
    connect_to_sos(register_in_mlflow(train_and_evaluate()))


defs = Definitions(jobs=[activity_anomaly_production])
