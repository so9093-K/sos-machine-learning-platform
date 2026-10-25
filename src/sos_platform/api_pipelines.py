"""신뢰된 등록형 Pipeline의 API-first 진입점."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal

from .scoring_observations import ranked_scoring_rows
from .pipeline_reference import PIPELINE_ID, definition, inspect_dataset
from .pipeline_runs import cancel_run, create_run, get_run, list_runs
from .security import Actor, require, resource_allowed
from .store import Store

READ_DATASET = Depends(require("dataset.read"))
READ_JOB = Depends(require("job.read"))
CREATE_JOB = Depends(require("job.create"))
CANCEL_JOB = Depends(require("job.cancel"))


class PipelineDefinitionOut(BaseModel):
    id: str
    version: str
    name: str
    description: str
    learning_type: str
    task_type: str
    topic: str
    algorithm: str
    required_time_column: str
    required_features: list[str]
    stages: list[str]
    model_reuse: bool
    label_requirement: str
    limitations: list[str]


class PipelinePartitionSizesOut(BaseModel):
    train: int
    calibration: int
    test: int


class PipelinePlanOut(BaseModel):
    timestamp_column: str
    train_end: str
    calibration_end: str
    partition_sizes: PipelinePartitionSizesOut


class PipelineCheckOut(BaseModel):
    pipeline_id: str
    pipeline_version: str
    dataset_id: str
    dataset_version: str
    ready: bool
    issues: list[str]
    feature_count: int
    plan: PipelinePlanOut | None


class PipelineJobOut(BaseModel):
    id: str
    kind: str
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    error_code: str | None = None
    result: dict[str, object] | None


class PipelineStageOut(BaseModel):
    name: str
    job: PipelineJobOut


class PipelineRunOut(BaseModel):
    id: str
    definition_id: str
    definition_version: str
    dataset_id: str
    dataset_version: str
    input_plan: PipelinePlanOut | None
    stage_index: int
    job_ids: dict[str, str]
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    error_code: str | None = None
    created_at: str
    updated_at: str
    current_stage: str | None
    stages: list[PipelineStageOut] = Field(default_factory=list)


class PipelineCandidateOut(BaseModel):
    row_index: int
    anomaly_score: float
    features: dict[str, float | int | None]
    observed_at: str | None = None
    observation_id: str | None = None


class PipelineCandidatesOut(BaseModel):
    items: list[PipelineCandidateOut]
    threshold: float
    next_cursor: str | None
    result_id: str


class PipelineStartRequest(BaseModel):
    dataset_id: str = Field(min_length=1)


def _visible(actor: Actor, item: dict | None) -> dict:
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    resource_allowed(actor, item["owner_actor"])
    return item



def _candidate_rows(store: Store, run: dict, limit: int, cursor: str | None, row_index: int | None = None) -> dict:
    by_stage = {entry["name"]: entry["job"] for entry in run["stages"]}
    score_id = by_stage["test"]["result"]["scoring_result_id"]
    policy_id = by_stage["policy"]["result"]["policy_revision_id"]
    scoring = store.get_scoring_result(score_id)
    policy = store.get_decision_policy_revision(policy_id)
    if (not scoring or not policy or scoring["owner_actor"] != run["owner_actor"]
            or policy["owner_actor"] != run["owner_actor"]):
        raise HTTPException(status_code=404, detail="Pipeline result unavailable")
    page = ranked_scoring_rows(
        store, scoring, limit, cursor, row_index=row_index, min_score=policy["threshold"],
    )
    return {**page, "threshold": policy["threshold"]}


def pipeline_router(store: Store, *, identity_mode: str) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/pipelines",
        tags=["Pipeline Catalog"],
        summary="등록형 Pipeline 목록 조회",
        response_model=list[PipelineDefinitionOut],
    )
    def pipelines(actor: Actor = READ_DATASET):
        return [definition()]

    @router.get(
        "/api/pipelines/{definition_id}",
        tags=["Pipeline Catalog"],
        summary="Pipeline 정의 조회",
        response_model=PipelineDefinitionOut,
    )
    def pipeline_detail(definition_id: str, actor: Actor = READ_DATASET):
        if definition_id != PIPELINE_ID:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return definition()

    @router.get(
        "/api/pipelines/{definition_id}/check",
        tags=["Pipeline Catalog"],
        summary="Pipeline 입력 Dataset 검사",
        response_model=PipelineCheckOut,
    )
    def pipeline_check(
        definition_id: str,
        dataset_id: str = Query(min_length=1),
        actor: Actor = READ_DATASET,
    ):
        if definition_id != PIPELINE_ID:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        dataset = store.get_dataset(dataset_id)
        _visible(actor, dataset)
        return inspect_dataset(store, dataset)

    @router.post(
        "/api/pipelines/{definition_id}/runs",
        tags=["Pipeline Runs"],
        summary="Pipeline 실행 제출",
        description="검사된 Dataset으로 등록형 Pipeline 실행을 접수합니다. Token 모드에는 Idempotency-Key가 필요합니다.",
        status_code=202,
        response_model=PipelineRunOut,
    )
    def submit_pipeline(
        definition_id: str,
        body: PipelineStartRequest,
        actor: Actor = CREATE_JOB,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key",
            min_length=8, max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        if definition_id != PIPELINE_ID:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        dataset = store.get_dataset(body.dataset_id)
        _visible(actor, dataset)
        if identity_mode == "token" and idempotency_key is None:
            raise HTTPException(status_code=400, detail="Idempotency-Key required")
        inspected = inspect_dataset(store, dataset)
        if not inspected["ready"]:
            raise HTTPException(status_code=422, detail=inspected["issues"])
        try:
            return create_run(store, dataset, actor.id, inspected, idempotency_key,
                              actor_type=actor.kind)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get(
        "/api/pipeline-runs",
        tags=["Pipeline Runs"],
        summary="Pipeline 실행 이력 조회",
        response_model=list[PipelineRunOut],
    )
    def pipeline_history(actor: Actor = READ_JOB):
        return list_runs(store, actor_id=None if actor.privileged else actor.id)

    @router.get(
        "/api/pipeline-runs/{run_id}",
        tags=["Pipeline Runs"],
        summary="Pipeline 실행 상태 조회",
        description="진행 상태와 단계별 Job 결과를 조회합니다. 학습 완료와 MLflow 모델 등록 완료는 별도 상태입니다.",
        response_model=PipelineRunOut,
    )
    def pipeline_run_detail(run_id: str, actor: Actor = READ_JOB):
        return _visible(actor, get_run(store, run_id))

    @router.post(
        "/api/pipeline-runs/{run_id}/cancel",
        tags=["Pipeline Runs"],
        summary="Pipeline 실행 취소 요청",
        response_model=PipelineRunOut,
    )
    def pipeline_cancel(run_id: str, actor: Actor = CANCEL_JOB):
        _visible(actor, get_run(store, run_id, include_jobs=False))
        return cancel_run(store, run_id)

    @router.get(
        "/api/pipeline-runs/{run_id}/candidates",
        tags=["Pipeline Runs"],
        summary="Pipeline 이상 후보 조회",
        response_model=PipelineCandidatesOut,
    )
    def pipeline_candidates(
        run_id: str, limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=160),
        row_index: int | None = Query(default=None, ge=0),
        actor: Actor = READ_JOB,
    ):
        run = _visible(actor, get_run(store, run_id))
        if run["status"] != "Succeeded":
            raise HTTPException(status_code=409, detail="Pipeline run is not completed")
        return _candidate_rows(store, run, limit, cursor, row_index=row_index)

    return router
