"""활동 이상 탐지 모델 생산 Pipeline. Dagster가 학습·평가·MLflow 등록을 직접 실행한다.

SOS DB·저장소·Job에 접근하지 않는다. 생산한 모델은 MLflow 모델 버전으로 등록하고, 연결 설정이
있으면 SOS API에 그 고정 버전의 연결을 요청한다. 전처리·학습·학습 근거 계산과 모델 패키지는 SOS와
같은 라이브러리 함수를 쓰므로, SOS에서 같은 의미로 표시되고 같은 점수를 낸다.
"""

import hashlib
import json
import math
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
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
from sos_platform.duckdb_spill import SpillBudget, order_statistics
from sos_platform.ml_if_config import IsolationForestConfig
from sos_platform.ml_input import NUMERIC
from sos_platform.ml_isolation_forest import TrainedIsolationForest, train_isolation_forest
from sos_platform.ml_model_package import ESTIMATOR_FILE, MANIFEST_FILE, package_isolation_forest
from sos_platform.ml_staged_matrix import (
    fit_staged_medians,
    staged_model_input,
    write_feature_matrix,
)
from sos_platform.ml_temporal_split import select_temporal_partition, utc_observation_time
from sos_platform.ml_training_evidence import feature_training_profile, training_score_summary
from sos_platform.mlflow_config import MLflowSettings
from sos_platform.mlflow_errors import MLflowDeliveryError
from sos_platform.mlflow_tracking import MLflowClient
from sos_platform.score_files import ScoreWriter, quantile_threshold_file, summarize_scores

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
    memory_mib: int = Field(
        default=2048, ge=256,
        description="외부 정렬·집계의 메모리 예산(MiB). 이 중 일부만 DuckDB에 주고 넘치면 디스크로 Spill한다",
    )
    stage_mib: int = Field(
        default=51200, ge=64,
        description="Run 임시 디렉터리에서 외부 정렬이 쓸 수 있는 디스크 크기(MiB)",
    )
    connect_to_sos: bool = Field(default=True, description="MLflow 등록 후 SOS에 고정 버전을 연결한다. 공급 CLI로 연결할 예정이면 false로 지정한다")
    registry_key: str = Field(default="default", description="연결한 판본을 관리할 SOS 모델 그룹")


def _pin_input(path: str, stage: Path) -> tuple[Path, str]:
    """입력 파일을 Run 임시 디렉터리로 복사하며 해시한다. Run은 이 사본만 읽는다.

    원본 경로의 파일이 Run 도중 바뀌어도 학습·평가는 고정한 사본을 쓰고, 기록하는 SHA-256은 그 사본의
    값이다. 읽기 전용 마운트는 호스트 쪽 변경까지 막지 않는다.
    """
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise Failure("data_path는 .csv 또는 .parquet이어야 합니다.")
    if not source.is_file():
        raise Failure("data_path는 Run 환경에서 읽을 수 있는 파일이어야 합니다.")
    pinned = stage / f"source{suffix}"
    digest = hashlib.sha256()
    with source.open("rb") as reader, pinned.open("wb") as writer:
        for chunk in iter(lambda: reader.read(1_048_576), b""):
            digest.update(chunk)
            writer.write(chunk)
    return pinned, digest.hexdigest()


def _scan(path: Path) -> pl.LazyFrame:
    return pl.scan_parquet(path) if path.suffix == ".parquet" else pl.scan_csv(path)


def _nearest_rank(count: int, fraction: float) -> int:
    """오름차순 순위 중 분위 위치에 가장 가까운 순위(반올림)."""
    return math.floor((count - 1) * fraction + 0.5)


def _score_partition(model: TrainedIsolationForest, scan: pl.LazyFrame, path: Path) -> int:
    """구간을 배치로 읽어 이상 점수를 파일에 쓴다. 결측은 학습 중앙값으로 대체하고 무한값은 거부한다."""
    with ScoreWriter(path) as writer:
        for frame in scan.select(ACTIVITY_FEATURES).collect_batches(
            chunk_size=8192, maintain_order=True,
        ):
            values = np.asarray(frame.to_numpy(), dtype=np.float64)
            if np.isinf(values).any():
                raise Failure("활동 Feature에 무한값이 있습니다.")
            writer.write(model.score_anomalies(values))
    return writer.count


@op(description="시간순 Train·Calibration·Test로 나누어 Isolation Forest를 학습하고, Calibration 점수로 판정 기준을 정해 Test 구간을 평가합니다. 결과를 SOS 모델 패키지로 만듭니다.")
def train_and_evaluate(context: OpExecutionContext, config: ProductionConfig) -> dict:
    """입력 크기와 관계없이 같은 경로로 생산한다.

    입력 파일은 Run 임시 디렉터리에 복사·해시해 고정하고 그 사본만 배치로 읽는다. 시간 경계는 외부 정렬로 정하고, 학습 행렬은
    디스크 Memmap에 기록하며, Calibration·Test 점수는 배치로 계산해 파일에서 임계값·평가를 구한다.
    모델 적합(fit)이 요구하는 메모리만 알고리즘의 실행 조건으로 남는다.
    """
    if config.train_fraction + config.calibration_fraction >= 1:
        raise Failure("train_fraction + calibration_fraction은 1보다 작아야 합니다.")
    features = list(ACTIVITY_FEATURES)
    stage_root = os.getenv("SOS_PIPELINE_STAGE_DIR")
    if stage_root:
        Path(stage_root).mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="activity-production-", dir=stage_root) as directory:
        stage = Path(directory)
        pinned, data_sha256 = _pin_input(config.data_path, stage)
        source = _scan(pinned)
        schema = source.collect_schema()
        missing = [name for name in (TIMESTAMP, *ACTIVITY_FEATURES) if name not in schema]
        if missing:
            raise Failure("필수 컬럼 없음: " + ", ".join(missing[:12]))
        if any(schema[name] not in NUMERIC for name in ACTIVITY_FEATURES):
            raise Failure("활동 Feature는 숫자형이어야 합니다.")
        budget = SpillBudget(
            stage=stage, memory_mib=config.memory_mib, max_stage_bytes=config.stage_mib * 1024 * 1024,
            stage_error="SOS-ML-TRAIN-STAGE-LIMIT", capacity_error="SOS-ML-TRAIN-CAPACITY",
        )
        staged_path = stage / "input.parquet"
        source.select(TIMESTAMP, *features).sink_parquet(staged_path)
        staged = pl.scan_parquet(staged_path)
        events_path = stage / "events.parquet"
        staged.select(
            utc_observation_time(staged, TIMESTAMP).dt.epoch("ms").alias("event_ms")
        ).sink_parquet(events_path)
        count, invalid = pl.scan_parquet(events_path).select(
            pl.len(), pl.col("event_ms").null_count(),
        ).collect(engine="streaming").row(0)
        if invalid or count < 3:
            raise Failure("ts의 UTC 오프셋·유효값·행 수를 확인하세요.")
        train_ms, calibration_ms = order_statistics(
            events_path, "event_ms",
            [_nearest_rank(count, config.train_fraction),
             _nearest_rank(count, config.train_fraction + config.calibration_fraction)],
            budget,
        )
        # 경계 시각의 행은 앞 구간에 둔다(경계는 다음 구간의 시작 시각).
        bounds = {
            "timestamp_column": TIMESTAMP,
            "train_end": datetime.fromtimestamp(train_ms / 1000, tz=UTC) + timedelta(milliseconds=1),
            "calibration_end": datetime.fromtimestamp(calibration_ms / 1000, tz=UTC)
            + timedelta(milliseconds=1),
        }
        try:
            train_scan, split = select_temporal_partition(staged, partition="train", **bounds)
            calibration_scan, _ = select_temporal_partition(staged, partition="calibration", **bounds)
            test_scan, _ = select_temporal_partition(staged, partition="test", **bounds)
            train = write_feature_matrix(
                train_scan, features, schema, stage / "train.float64", missing_policy="train_median",
            )
        except ValueError as exc:
            raise Failure(f"시간 분할 실패: {exc}") from exc

        config_params = IsolationForestConfig(
            n_estimators=config.n_estimators, random_state=config.random_state,
        )
        preprocessing = fit_staged_medians(train, features)
        transformed = staged_model_input(train, preprocessing, stage=stage)
        trained = train_isolation_forest(transformed, config_params)
        summary, _ = training_score_summary(trained, transformed, budget)
        profile = feature_training_profile(transformed, features)
        model = TrainedIsolationForest(
            trained.estimator, config_params, len(features), preprocessing=preprocessing,
        )
        _score_partition(model, calibration_scan, stage / "calibration-scores.parquet")
        threshold = quantile_threshold_file(
            stage / "calibration-scores.parquet", config.alert_fraction, budget,
        )
        _score_partition(model, test_scan, stage / "test-scores.parquet")
        test_evaluation = summarize_scores(stage / "test-scores.parquet", threshold=threshold)
        del train, transformed
    rows = {key: split[f"{key}_rows"] for key in ("train", "calibration", "test")}
    files = package_isolation_forest(
        model,
        feature_dtypes=[str(schema[name]) for name in features],
        training_score_summary=summary,
        feature_training_profile=profile,
        training={
            "producer": PRODUCER,
            "data_reference": config.data_path,
            "data_sha256": data_sha256,
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
