"""MLflow 모델 버전 연결 HTTP 계약. 모델 생산은 SOS 밖의 Pipeline이 맡고 SOS는 고정 버전을 연결한다."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .api_access import ResourceAccess
from .schemas import MLModelImportJobOut, MLModelImportRequest
from .security import Actor, require
from .store import Store

MANAGE_MODEL_DEP = Depends(require("model.manage"))
READ_JOB_DEP = Depends(require("job.read"))
CANCEL_JOB_DEP = Depends(require("job.cancel"))


def model_import_router(store: Store, access: ResourceAccess) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/ml/models/imports",
        tags=["Models"],
        summary="MLflow 모델 버전 연결",
        description=(
            "MLflow에 등록된 고정 모델 버전을 다시 학습하지 않고 SOS Model Revision으로 연결합니다. "
            "Worker가 모델 패키지(sos_model.json·estimator.skops)를 검증하고 SOS Runtime에서 검증 "
            "점수를 재현한 뒤 게시합니다. 202 Accepted는 연결 완료를 의미하지 않습니다."
        ),
        status_code=202,
        response_model=MLModelImportJobOut,
    )
    def create_model_import(
        body: MLModelImportRequest,
        request: Request,
        actor: Actor = MANAGE_MODEL_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_import",
            dataset_id=None,
            spec={
                "algorithm": "isolation_forest",
                "mlflow_model": body.mlflow_model,
                "mlflow_version": body.mlflow_version,
                "registry_key": body.registry_key,
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/api/ml/models/imports/{job_id}",
        tags=["Models"],
        summary="MLflow 모델 버전 연결 상태 조회",
        description="연결이 성공하면 `result.model_revision_id`가 게시된 Model Revision입니다.",
        response_model=MLModelImportJobOut,
    )
    def get_model_import(job_id: str, actor: Actor = READ_JOB_DEP):
        return existing_model_import(job_id, actor)

    @router.post(
        "/api/ml/models/imports/{job_id}/cancel",
        tags=["Models"],
        summary="MLflow 모델 버전 연결 취소 요청",
        description="취소 요청이며 실제 중단은 조회로 확인합니다. 이미 게시된 Model Revision은 되돌리지 않습니다.",
        response_model=MLModelImportJobOut,
    )
    def cancel_model_import(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        existing_model_import(job_id, actor)
        item = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if item is None:
            raise HTTPException(status_code=404, detail="모델 연결 작업을 찾을 수 없습니다.")
        return item

    def existing_model_import(job_id: str, actor: Actor) -> dict:
        item = access.job(job_id, actor)
        if item["kind"] != "ml_import":
            raise HTTPException(status_code=404, detail="모델 연결 작업을 찾을 수 없습니다.")
        return item

    return router
