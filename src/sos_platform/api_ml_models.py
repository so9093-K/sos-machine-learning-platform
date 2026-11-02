"""모델 판본·MLflow 실험 기록·Registry 조회와 MLflow 전송 상태 API."""

from fastapi import APIRouter, Depends, HTTPException, Query

from .api_access import ResourceAccess
from .errors import RuntimeConfigurationError
from .mlflow_config import MLflowSettings
from .mlflow_errors import MLflowDeliveryError
from .mlflow_read import read_experiments, read_runs
from .mlflow_tracking import MLflowClient
from .schemas import (
    MLflowExperimentPageOut,
    MLflowExportStatusOut,
    MLflowRunPageOut,
    ModelRevisionOut,
    PublicModelRevisionOut,
)
from .security import Actor, require
from .store import Store

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
READ_JOB_DEP = Depends(require("job.read"))


def management_client() -> MLflowClient:
    """MLflow 관리 연결이 없으면 503으로 거부한다."""
    try:
        settings = MLflowSettings.from_env()
    except RuntimeConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="MLflow 관리 서비스 설정을 확인하세요."
        ) from exc
    if settings is None:
        raise HTTPException(status_code=503, detail="MLflow 관리 서비스를 사용할 수 없습니다.")
    return MLflowClient(settings)


def ml_model_router(store: Store, access: ResourceAccess) -> APIRouter:
    """모델 판본은 SOS, 실험·Registry는 MLflow가 정본이며 이 경로는 조회만 한다."""
    router = APIRouter()

    @router.get(
        "/api/ml/tracking/jobs/{job_id}",
        tags=["Experiments"],
        summary="MLflow 기록 및 등록 상태 조회",
        response_model=MLflowExportStatusOut,
    )
    def get_mlflow_tracking_status(job_id: str, actor: Actor = READ_JOB_DEP):
        item = access.job(job_id, actor)
        if item["kind"] not in (
            "ml_train", "ml_import", "ml_score", "ml_policy", "ml_evaluate", "ml_explain",
        ):
            raise HTTPException(status_code=404, detail="ML 작업을 찾을 수 없습니다.")
        status = store.tracking_status(job_id)
        return status or {"job_id": job_id, "state": "Disabled"}


    @router.get(
        "/api/ml/experiments",
        tags=["Experiments"],
        summary="MLflow 실험 목록 조회",
        response_model=MLflowExperimentPageOut,
    )
    def list_ml_experiments(
        page_token: str | None = Query(default=None, max_length=2048),
        actor: Actor = READ_JOB_DEP,
    ):
        try:
            return read_experiments(management_client(), actor.id, page_token=page_token)
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get(
        "/api/ml/experiments/runs",
        tags=["Experiments"],
        summary="MLflow 실행 기록 조회",
        response_model=MLflowRunPageOut,
    )
    def list_ml_runs(
        experiment_id: str | None = Query(default=None, pattern=r"^[0-9]+$"),
        page_token: str | None = Query(default=None, max_length=2048),
        actor: Actor = READ_JOB_DEP,
    ):
        try:
            return read_runs(
                management_client(), store, actor.id,
                experiment_id=experiment_id, page_token=page_token,
            )
        except MLflowDeliveryError as exc:
            if str(exc) == "SOS-MLFLOW-EXPERIMENT-NOT-FOUND":
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get(
        "/api/ml/models/{revision_id}",
        tags=["Models"],
        summary="모델 실행 판본 조회",
        description="고정된 모델 판본의 사용 정보를 조회합니다. 학습 출처·아티팩트 저장 근거·매개변수·"
        "학습 규모·선택·결측 정책과 학습 점수 집계는 소유자·관리자만 조회합니다.",
        response_model=ModelRevisionOut | PublicModelRevisionOut,
    )
    def get_model_revision(revision_id: str, actor: Actor = READ_JOB_DEP):
        return access.model_response(access.readable_model(revision_id, actor), actor)

    return router
