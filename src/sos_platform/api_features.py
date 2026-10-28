"""Feast 등록 Feature Service와 역사적 조회를 SOS Job 및 Actor 권한에 연결한다."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .feast_features import FeatureDefinitionRequest, FeatureRetrievalRequest
from .schemas import JobOut
from .security import Actor, require
from .store import Store

CREATE_JOB = Depends(require("job.create"))
READ_JOB = Depends(require("job.read"))


def feast_router(
    store: Store,
    *,
    existing_dataset: Callable[[str, Actor], dict],
    require_submission_key: Callable[[str | None], None],
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/features/services/jobs",
        tags=["Features"],
        status_code=202,
        response_model=JobOut,
        summary="재사용 가능한 Feast Feature Service 등록",
    )
    def create_feature_service(
        body: FeatureDefinitionRequest,
        request: Request,
        actor: Actor = CREATE_JOB,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key",
            min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    ):
        source = existing_dataset(body.feature_dataset_id, actor)
        require_submission_key(idempotency_key)
        return store.create_job(
            kind="feature_register",
            dataset_id=source["id"],
            spec={**body.model_dump(mode="json"), "feature_dataset_version": source["version"]},
            owner_actor=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id, idempotency_key=idempotency_key,
        )

    @router.get(
        "/api/features/services/registrations",
        tags=["Features"],
        response_model=list[JobOut],
        summary="내 Feature Service 등록 Job 이력 조회",
        description="SOS Job 이력을 반환합니다. Feast Registry의 현재 사용 가능 상태를 대체하지 않습니다.",
    )
    def list_feature_registrations(actor: Actor = READ_JOB):
        return store.jobs(limit=100, actor_id=actor.id, kind="feature_register")

    @router.post(
        "/api/features/retrieval/jobs",
        tags=["Features"],
        status_code=202,
        response_model=JobOut,
        summary="등록 Feast Feature Service의 역사적 조회 Dataset 게시",
    )
    def create_feature_retrieval(
        body: FeatureRetrievalRequest,
        request: Request,
        actor: Actor = CREATE_JOB,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key",
            min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    ):
        registration = store.get_job(body.registration_job_id)
        if (
            registration is None or registration["kind"] != "feature_register"
            or registration["status"] != "Succeeded"
            or registration["owner_actor"] != actor.id
            or not (registration.get("result") or {}).get("feature_service")
        ):
            raise HTTPException(status_code=404, detail="사용 가능한 Feature Service 등록 기록이 없습니다.")
        source = existing_dataset(registration["dataset_id"], actor)
        entity = existing_dataset(body.entity_dataset_id, actor)
        if source["version"] != registration["spec"]["feature_dataset_version"]:
            raise HTTPException(status_code=409, detail="등록 Feature Source 판본을 사용할 수 없습니다.")
        require_submission_key(idempotency_key)
        return store.create_job(
            kind="feature_retrieve",
            dataset_id=source["id"],
            spec={**body.model_dump(mode="json"),
                  "feature_dataset_version": source["version"],
                  "entity_dataset_version": entity["version"]},
            owner_actor=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id, idempotency_key=idempotency_key,
        )

    return router
