"""ML Training HTTP 계약. 앱의 인증·소유권·멱등성 검사를 그대로 사용한다."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .ml_input import TRAINING_SELECTION_CURRENT, TRAINING_SELECTION_LEGACY
from .pagination import decode_cursor, page_from_rows
from .schemas import (
    MLForkRequest,
    MLTrainingInputPolicy,
    MLTrainingJobOut,
    MLTrainingJobPageOut,
    MLTrainRequest,
)
from .security import Actor, require
from .store import Store

CREATE_JOB_DEP = Depends(require("job.create"))
READ_JOB_DEP = Depends(require("job.read"))
CANCEL_JOB_DEP = Depends(require("job.cancel"))


def training_router(
    store: Store,
    *,
    existing_dataset: Callable[[str, Actor], dict],
    existing_job: Callable[[str, Actor], dict],
    require_submission_key: Callable[[str | None], None],
) -> APIRouter:
    """Training·Fork·조회·취소를 앱의 기존 공통 검증 규칙에 연결한다."""
    router = APIRouter()

    @router.post(
        "/api/ml/train/jobs",
        tags=["Training"],
        summary="Isolation Forest 학습 작업 제출",
        description="Dataset 판본과 모델 설정을 고정해 비동기 학습을 접수합니다. 202 Accepted는 학습 완료를 의미하지 않습니다.",
        status_code=202,
        response_model=MLTrainingJobOut,
    )
    def create_training_job(
        body: MLTrainRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        dataset = existing_dataset(body.dataset_id, actor)
        require_submission_key(idempotency_key)
        from .ml_isolation_forest import IsolationForestConfig

        try:
            IsolationForestConfig(**body.params.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="학습 매개변수가 올바르지 않습니다.") from exc
        return store.create_job(
            kind="ml_train",
            dataset_id=dataset["id"],
            spec={
                "algorithm": "isolation_forest",
                "registry_key": body.registry_key,
                "experiment_title": body.experiment_title.strip(),
                "dataset_version": dataset["version"],
                "features": body.features,
                "params": body.params.model_dump(),
                "training_input": body.training_input.model_dump(),
                "missing_policy": body.missing_policy,
                "temporal_split": (
                    body.temporal_split.model_dump(mode="json")
                    if body.temporal_split is not None else None
                ),
                "selection_algorithm_version": TRAINING_SELECTION_CURRENT,
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    def existing_ml_training_job(job_id: str, actor: Actor) -> dict:
        item = existing_job(job_id, actor)
        if item["kind"] != "ml_train":
            raise HTTPException(status_code=404, detail="학습 작업을 찾을 수 없습니다.")
        return item

    @router.post(
        "/api/ml/train/jobs/{source_job_id}/fork",
        tags=["Training"],
        summary="기존 학습 설정으로 새 학습 시작",
        status_code=202,
        response_model=MLTrainingJobOut,
    )
    def fork_training_job(
        source_job_id: str,
        body: MLForkRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        source = existing_ml_training_job(source_job_id, actor)
        if source["status"] != "Succeeded":
            raise HTTPException(status_code=409, detail="완료된 학습 작업만 복제할 수 있습니다.")
        dataset = existing_dataset(source["dataset_id"], actor)
        if dataset["version"] != source["spec"]["dataset_version"]:
            raise HTTPException(status_code=409, detail="학습 Dataset 판본을 사용할 수 없습니다.")
        require_submission_key(idempotency_key)
        params = body.params.model_dump() if body.params is not None else source["spec"]["params"]
        from .ml_isolation_forest import IsolationForestConfig

        try:
            IsolationForestConfig(**params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="모델 매개변수가 올바르지 않습니다.") from exc
        return store.create_job(
            kind="ml_train",
            dataset_id=dataset["id"],
            spec={
                "algorithm": source["spec"]["algorithm"],
                "registry_key": body.registry_key or source["spec"].get("registry_key", "default"),
                "experiment_title": source["spec"].get("experiment_title", "기본 실험"),
                "dataset_version": source["spec"]["dataset_version"],
                "features": source["spec"]["features"],
                "params": params,
                "training_input": MLTrainingInputPolicy.model_validate(
                    source["spec"].get("training_input", {})
                ).model_dump(),
                "selection_algorithm_version": source["spec"].get(
                    "selection_algorithm_version", TRAINING_SELECTION_LEGACY
                ),
                "missing_policy": source["spec"].get("missing_policy", "reject"),
                "temporal_split": source["spec"].get("temporal_split"),
                "forked_from_job_id": source_job_id,
            },
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/api/ml/train/jobs",
        tags=["Training"],
        summary="학습 작업 목록 조회",
        response_model=MLTrainingJobPageOut,
    )
    def list_ml_training_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        rows = store.jobs(
            limit=limit + 1,
            before=decode_cursor(cursor, "Job"),
            actor_id=None if actor.privileged else actor.id,
            kind="ml_train",
        )
        return page_from_rows(rows, limit)

    @router.get(
        "/api/ml/train/jobs/{job_id}",
        tags=["Training"],
        summary="학습 작업 상세 조회",
        response_model=MLTrainingJobOut,
    )
    def get_ml_training_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return existing_ml_training_job(job_id, actor)

    @router.post(
        "/api/ml/train/jobs/{job_id}/cancel",
        tags=["Training"],
        summary="학습 작업 취소 요청",
        response_model=MLTrainingJobOut,
    )
    def cancel_ml_training_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        existing_ml_training_job(job_id, actor)
        item = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if item is None:
            raise HTTPException(status_code=404, detail="학습 작업을 찾을 수 없습니다.")
        return item

    return router
