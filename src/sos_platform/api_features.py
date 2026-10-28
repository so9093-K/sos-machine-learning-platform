"""Feast 등록 Feature Service와 역사적 조회를 SOS Job 및 Actor 권한에 연결한다."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .feast_features import FeatureDefinitionRequest, FeatureRetrievalRequest, feature_identity
from .pagination import decode_cursor, page_from_rows
from .schemas import JobOut, FeatureRegistrationPageOut
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
            spec={
                **body.model_dump(mode="json"),
                "feature_dataset_version": source["version"],
                "feature_service_name": feature_identity(body, source)[2],
                "feature_source_owner": source.get("owner_actor"),
            },
            owner_actor=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id, idempotency_key=idempotency_key,
        )

    @router.get(
        "/api/features/services/registrations",
        tags=["Features"],
        response_model=FeatureRegistrationPageOut,
        summary="내 Feature Service 등록 Job 이력 조회",
        description="SOS Job 이력을 반환합니다. Feast Registry의 현재 사용 가능 상태를 대체하지 않습니다.",
    )
    def list_feature_registrations(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB,
    ):
        rows = store.jobs(
            limit=limit + 1,
            before=decode_cursor(cursor, "Feature Registration"),
            actor_id=actor.id,
            kind="feature_register",
        )
        page = page_from_rows(rows, limit)
        retired = store.retired_feature_registration_ids([item["id"] for item in page["items"]])
        for item in page["items"]:
            item["retired"] = item["id"] in retired
        return page

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
        if body.registration_job_id in store.blocked_feature_registration_ids([body.registration_job_id]):
            raise HTTPException(status_code=409, detail="폐기된 Feature Service 등록은 조회할 수 없습니다.")
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
                  "entity_dataset_version": entity["version"],
                  "feature_source_owner": source.get("owner_actor"),
                  "entity_dataset_owner": entity.get("owner_actor")},
            owner_actor=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id, idempotency_key=idempotency_key,
        )

    @router.post(
        "/api/features/services/{registration_job_id}/retirement/jobs",
        tags=["Features"], status_code=202, response_model=JobOut,
        summary="Feature Service 등록 폐기 및 안전한 Registry 정리",
    )
    def retire_feature_registration(
        registration_job_id: str,
        request: Request,
        actor: Actor = CREATE_JOB,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key",
            min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    ):
        registration = store.get_job(registration_job_id)
        if (
            registration is None or registration["kind"] != "feature_register"
            or registration["owner_actor"] != actor.id
        ):
            raise HTTPException(status_code=404, detail="Feature Service 등록을 찾을 수 없습니다.")
        if registration["status"] not in ("Succeeded", "Failed", "Cancelled"):
            raise HTTPException(status_code=409, detail="진행 중인 Feature 등록은 먼저 종료해야 합니다.")
        if registration_job_id in store.blocked_feature_registration_ids([registration_job_id]):
            raise HTTPException(status_code=409, detail="이미 폐기된 Feature 등록입니다.")
        source = existing_dataset(registration["dataset_id"], actor)
        if source["version"] != registration["spec"]["feature_dataset_version"]:
            raise HTTPException(status_code=409, detail="Feature Source 판본이 달라졌습니다.")
        require_submission_key(idempotency_key)
        return store.create_job(
            kind="feature_retire", dataset_id=source["id"],
            spec={
                "registration_job_id": registration_job_id,
                "feature_dataset_version": source["version"],
                "feature_source_owner": source.get("owner_actor"),
            },
            owner_actor=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id, idempotency_key=idempotency_key,
        )

    return router
