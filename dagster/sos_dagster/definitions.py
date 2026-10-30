"""SOS의 독립 ML Job을 Dagster Workflow로 조합한다.

Dagster는 단계 의존성과 Workflow Run을 소유한다. Job/Attempt와 결과 게시의
정본은 SOS Store이고 모델 관리의 정본은 MLflow다.
"""
import os
import time

from sqlalchemy import text

from dagster import (
    AssetMaterialization,
    AssetSpec,
    DagsterRunStatus,
    DefaultSensorStatus,
    Definitions,
    Failure,
    OpExecutionContext,
    RunStatusSensorContext,
    in_process_executor,
    job,
    op,
    run_status_sensor,
)
from sos_platform.analysis_topics import ACTIVITY_FEATURES
from sos_platform.dagster_workflows import workflow_job_key
from sos_platform.pipeline_reference import PIPELINE_ID, PIPELINE_VERSION, STAGES
from sos_platform.store import Store


def _submission(context: OpExecutionContext, store: Store) -> dict:
    submission_id = context.run.tags.get("sos/submission_id")
    if not submission_id:
        raise Failure("SOS에서 인증·승인된 제출 ID가 없는 실행은 허용하지 않습니다.")
    with store.connection() as conn:
        row = conn.execute(
            text("SELECT * FROM workflow_submissions WHERE id=:id"),
            {"id": submission_id},
        ).mappings().one_or_none()
    if row is None or row["definition_id"] != PIPELINE_ID:
        raise Failure("유효한 SOS 분석 제출 기록이 없습니다.")
    if row["cancel_requested"]:
        raise Failure("사용자가 취소한 SOS Workflow 제출은 실행할 수 없습니다.")
    dataset = store.get_dataset(row["dataset_id"])
    if dataset is None or dataset["version"] != row["dataset_version"]:
        raise Failure("고정된 입력 Dataset 판본을 확인할 수 없습니다.")
    return dict(row)


def _job(context: OpExecutionContext, row: dict, stage: str, spec: dict, kind: str) -> dict:
    store = Store(os.environ.get("SOS_HOME", "/var/lib/sos"))
    key = workflow_job_key(row["id"], stage)
    job = store.create_job(
        kind=kind,
        dataset_id=row["dataset_id"],
        spec=spec,
        owner_actor=row["owner_actor"],
        actor_type=row["owner_actor_type"],
        idempotency_key=key,
        workflow_source=(row["id"], stage, context.dagster_run.root_run_id or context.run_id),
    )
    context.log.info("%s SOS Job: %s", stage, job["id"])
    try:
        while job["status"] in ("Queued", "Running"):
            time.sleep(1)
            job = store.get_job(job["id"])
            if job is None:
                raise Failure("SOS Job 기록이 사라졌습니다.")
        if job["status"] != "Succeeded":
            raise Failure(f"SOS {stage} 실패: {job.get('error_code') or job['status']}")
        return job
    finally:
        # Dagster 취소·강제 종료 시 미완료 SOS Job을 방치하지 않도록 요청한다.
        # 강제 SIGKILL 이후 누락된 취소는 SOS API의 취소·복구 경로에서 처리한다.
        if job is not None and job["status"] in ("Queued", "Running"):
            store.request_cancel(
                job["id"], actor_id=row["owner_actor"], actor_type=row["owner_actor_type"]
            )


def _base_state(context: OpExecutionContext) -> tuple[Store, dict]:
    store = Store(os.environ.get("SOS_HOME", "/var/lib/sos"))
    store.initialize()
    return store, _submission(context, store)


@op(description="고정된 Dataset 판본의 입력 조건을 검사하고 Train·Calibration·Test 시간 분할을 확정하는 SOS Job을 실행합니다.")
def preflight(context: OpExecutionContext) -> dict:
    store, row = _base_state(context)
    result = _job(context, row, "preflight", {
        "dataset_version": row["dataset_version"],
        "definition_id": PIPELINE_ID,
        "definition_version": PIPELINE_VERSION,
    }, "ml_pipeline_preflight")
    plan = (result["result"] or {}).get("plan")
    if not isinstance(plan, dict) or not all(
        key in plan for key in ("timestamp_column", "train_end", "calibration_end")
    ):
        raise Failure("Preflight가 유효한 시간 분할 계획을 반환하지 않았습니다.")
    return {"plan": plan}


@op(description="Train 구간으로 Isolation Forest를 학습하는 SOS Job을 실행하고, MLflow Registry 등록이 확인될 때까지 기다립니다.")
def training(context: OpExecutionContext, state: dict) -> dict:
    store, row = _base_state(context)
    plan = state["plan"]
    scope = {
        "timestamp_column": plan["timestamp_column"],
        "train_end": plan["train_end"],
        "calibration_end": plan["calibration_end"],
    }
    result = _job(context, row, "training", {
        "algorithm": "isolation_forest",
        "dataset_version": row["dataset_version"],
        "features": list(ACTIVITY_FEATURES),
        "params": {
            "n_estimators": 100, "max_samples": "auto",
            "max_features": 1.0, "bootstrap": False, "random_state": 42,
        },
        "training_input": {"mode": "all"},
        "missing_policy": "train_median",
        "temporal_split": scope,
    }, "ml_train")
    model_id = (result["result"] or {}).get("model_revision_id")
    if not model_id:
        raise Failure("Training Job이 Model Revision을 생성하지 않았습니다.")
    # Registry 확인 전 모델을 Scoring에 사용하지 않는다.
    while True:
        model = store.get_model_revision(model_id)
        if model is None:
            raise Failure("학습한 Model Revision의 게시 근거가 없습니다.")
        if model.get("registry_version"):
            break
        if store.tracking_status(result["id"]) is None:
            raise Failure("MLflow Registry 등록이 설정되어 있지 않습니다.")
        time.sleep(2)
    context.log_event(AssetMaterialization(
        asset_key="sos_activity_model",
        metadata={
            "model_revision_id": model_id,
            "dataset_id": row["dataset_id"],
            "dataset_version": row["dataset_version"],
            "registry_version": str(model["registry_version"]),
        },
    ))
    return {**state, "model_id": model_id}


@op(description="학습한 모델로 Calibration 구간을 점수화하는 SOS Job을 실행합니다.")
def calibration(context: OpExecutionContext, state: dict) -> dict:
    _, row = _base_state(context)
    scope = {name: state["plan"][name] for name in
             ("timestamp_column", "train_end", "calibration_end")}
    result = _job(context, row, "calibration", {
        "model_revision_id": state["model_id"],
        "model_owner_actor": row["owner_actor"],
        "dataset_version": row["dataset_version"],
        "label_column": None,
        "observation_id_column": state["plan"].get("observation_id_column"),
        "temporal_scope": {**scope, "partition": "calibration"},
    }, "ml_score")
    return {**state, "calibration_id": result["result"]["scoring_result_id"]}


@op(description="Calibration 점수의 분위수로 판정 기준(Decision Policy)을 만드는 SOS Job을 실행합니다.")
def policy(context: OpExecutionContext, state: dict) -> dict:
    _, row = _base_state(context)
    result = _job(context, row, "policy", {
        "scoring_result_id": state["calibration_id"],
        "mode": "quantile", "alert_fraction": 0.005,
        "threshold": None, "source_owner_actor": row["owner_actor"],
    }, "ml_policy")
    return {**state, "policy_id": result["result"]["policy_revision_id"]}


@op(description="학습한 모델로 Test 구간을 점수화하는 SOS Job을 실행합니다.")
def test(context: OpExecutionContext, state: dict) -> dict:
    _, row = _base_state(context)
    scope = {name: state["plan"][name] for name in
             ("timestamp_column", "train_end", "calibration_end")}
    result = _job(context, row, "test", {
        "model_revision_id": state["model_id"],
        "model_owner_actor": row["owner_actor"],
        "dataset_version": row["dataset_version"],
        "label_column": None,
        "observation_id_column": state["plan"].get("observation_id_column"),
        "temporal_scope": {**scope, "partition": "test"},
    }, "ml_score")
    score_id = result["result"]["scoring_result_id"]
    context.log_event(AssetMaterialization(
        asset_key="sos_activity_scores",
        metadata={"scoring_result_id": score_id,
                  "model_revision_id": state["model_id"]},
    ))
    return {**state, "test_id": score_id}


@op(description="Test 점수에 판정 기준을 적용해 이상 후보를 확정하는 SOS Job을 실행합니다.")
def evaluation(context: OpExecutionContext, state: dict) -> None:
    _, row = _base_state(context)
    result = _job(context, row, "evaluation", {
        "scoring_result_id": state["test_id"],
        "policy_revision_id": state["policy_id"],
        "source_owner_actor": row["owner_actor"],
        "policy_owner_actor": row["owner_actor"],
        "reference_owner_actor": row["owner_actor"],
    }, "ml_evaluate")
    context.log_event(AssetMaterialization(
        asset_key="sos_activity_evaluation",
        metadata={
            "evaluation_revision_id": result["result"]["evaluation_revision_id"],
            "scoring_result_id": state["test_id"],
        },
    ))
    return None


@job(
    executor_def=in_process_executor,
    tags={"sos/workflow": PIPELINE_ID},
    description=(
        "SOS 활동 이상 탐지 분석. 실행은 SOS API로만 제출하며 태그 sos/submission_id가 SOS 분석 ID입니다. "
        "각 단계는 같은 SOS Job을 멱등 키로 재사용하므로 여기서 Re-execute하면 완료된 단계 결과를 그대로 쓰고 "
        "실패한 단계는 같은 이유로 다시 실패합니다. 다시 실행하려면 SOS에서 새 분석을 시작하세요."
    ),
)
def activity_anomaly_workflow():
    evaluation(test(policy(calibration(training(preflight())))))



def _cancel_orphaned_sos_jobs(context: RunStatusSensorContext) -> None:
    """Dagster UI의 종료·실패 후에도 독립 SOS Job이 남지 않도록 Dagster가 정리한다.

    성공한 Job·완료된 결과는 수정하지 않는다. Dagster 자체 Run 상태를
    SOS DB에 복제하지 않고, 정해진 Run 이벤트에만 반응한다.
    """
    submission_id = context.dagster_run.tags.get("sos/submission_id")
    if not submission_id:
        return
    store = Store(os.environ.get("SOS_HOME", "/var/lib/sos"))
    with store.connection() as conn:
        row = conn.execute(
            text("SELECT owner_actor,owner_actor_type FROM workflow_submissions WHERE id=:id"),
            {"id": submission_id},
        ).mappings().one_or_none()
    if row is None:
        return
    for stage in STAGES:
        with store.connection() as conn:
            job_id = conn.execute(text(
                "SELECT job_id FROM job_submissions "
                "WHERE actor_id=:actor AND submission_key=:key"
            ), {
                "actor": row["owner_actor"],
                "key": workflow_job_key(submission_id, stage),
            }).scalar_one_or_none()
        if not job_id:
            continue
        job = store.get_job(job_id)
        if job and job["status"] in ("Queued", "Running"):
            # 취소와 Worker의 완료가 경합할 수 있다. 이미 종료된 Job을 덮어쓰지 않는다.
            store.request_cancel(
                job_id, actor_id=row["owner_actor"],
                actor_type=row["owner_actor_type"],
            )


@run_status_sensor(
    run_status=DagsterRunStatus.CANCELED,
    monitored_jobs=[activity_anomaly_workflow],
    default_status=DefaultSensorStatus.RUNNING,
)
def cancel_sos_jobs_after_run_cancel(context: RunStatusSensorContext) -> None:
    _cancel_orphaned_sos_jobs(context)


@run_status_sensor(
    run_status=DagsterRunStatus.FAILURE,
    monitored_jobs=[activity_anomaly_workflow],
    default_status=DefaultSensorStatus.RUNNING,
)
def cancel_sos_jobs_after_run_failure(context: RunStatusSensorContext) -> None:
    _cancel_orphaned_sos_jobs(context)


raw = AssetSpec("sos_activity_dataset", description="SOS가 관리하는 고정 Dataset 판본")
model = AssetSpec("sos_activity_model", deps=[raw],
                  description="SOS Model Revision / MLflow Registry의 외부 게시 근거")
scores = AssetSpec("sos_activity_scores", deps=[raw, model],
                   description="고정된 모델과 Dataset의 Test Scoring Result")
evaluation_asset = AssetSpec("sos_activity_evaluation", deps=[scores],
                             description="SOS Evaluation Revision 결과")

defs = Definitions(
    jobs=[activity_anomaly_workflow],
    sensors=[cancel_sos_jobs_after_run_cancel, cancel_sos_jobs_after_run_failure],
    assets=[raw, model, scores, evaluation_asset],
)
