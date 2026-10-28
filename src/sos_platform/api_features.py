"""Feast Historical Feature 조회를 SOS 권한과 Job/Attempt 계약에 연결한다."""

from fastapi import APIRouter, Depends, Header, Request
from collections.abc import Callable

from .feast_features import FeatureRetrievalRequest
from .schemas import JobOut
from .security import Actor, require
from .store import Store


CREATE_JOB = Depends(require("job.create"))


def feast_router(
    store: Store,
    *,
    existing_dataset: Callable[[str, Actor], dict],
    require_submission_key: Callable[[str | None], None],
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/features/retrieval/jobs",
        tags=["Features"],
        status_code=202,
        response_model=JobOut,
        summary="Feast Point-in-Time Feature 조회와 불변 Dataset 게시",
    )
    def create_feature_retrieval(
        body: FeatureRetrievalRequest,
        request: Request,
        actor: Actor = CREATE_JOB,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        source = existing_dataset(body.feature_dataset_id, actor)
        entity = existing_dataset(body.entity_dataset_id, actor)
        require_submission_key(idempotency_key)
        return store.create_job(
            kind="feature_retrieve",
            dataset_id=source["id"],
            spec={
                **body.model_dump(mode="json"),
                "feature_dataset_version": source["version"],
                "entity_dataset_version": entity["version"],
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    return router
