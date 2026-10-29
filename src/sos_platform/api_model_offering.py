"""개발·배포 과정이 준비한 모델 판본을 사용자의 적용 대상으로 제공하는 계약.

제공·중단은 모델을 소유한 자동화 계정이나 관리자가 API로 수행한다. 제공은 판본의
조회·새 계산만 허용하며 학습 Dataset·학습 Job·실험 기록의 소유권 검사는 유지한다.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from .pagination import decode_cursor, page_from_rows
from .schemas import ModelOfferingPageOut, ModelOfferingRequest, ModelOfferingStateOut
from .security import Actor, require
from .store import Store
from .store_models import ModelOfferingUnavailable

READ_JOB = Depends(require("job.read"))
MANAGE_MODEL = Depends(require("model.manage"))

ModelLookup = Callable[[str, Actor], dict]


def model_offering_router(
    store: Store,
    *,
    readable_model: ModelLookup,
    owned_model: ModelLookup,
) -> APIRouter:
    router = APIRouter()

    def offering_state(model: dict, actor: Actor) -> dict:
        offering = store.get_model_offering(model["id"]) or {}
        owned = model.get("owner_actor") == actor.id
        return {
            "offered": (
                bool(offering) and offering.get("withdrawn_at") is None
                and model["lifecycle_state"] == "Active" and bool(model.get("registry_version"))
            ),
            "description": offering.get("description"),
            "offered_by": offering.get("offered_by"),
            "offered_at": offering.get("offered_at"),
            "withdrawn_at": offering.get("withdrawn_at"),
            "owned": owned,
        }

    @router.get(
        "/api/ml/offered-models",
        tags=["Models"],
        summary="제공 중인 모델 판본 조회",
        description="제공 중인 활성 모델 판본을 최근 제공 순으로 반환합니다. "
        "학습 Dataset과 학습 기록의 열람 권한은 포함하지 않습니다.",
        response_model=ModelOfferingPageOut,
    )
    def list_offered_models(
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB,
    ):
        rows = store.list_model_offerings(
            limit=limit + 1, before=decode_cursor(cursor, "제공 모델")
        )
        return page_from_rows(rows, limit)

    @router.get(
        "/api/ml/models/{revision_id}/offering",
        tags=["Models"],
        summary="모델 판본의 제공 상태 조회",
        response_model=ModelOfferingStateOut,
    )
    def get_model_offering(revision_id: str, actor: Actor = READ_JOB):
        return offering_state(readable_model(revision_id, actor), actor)

    @router.put(
        "/api/ml/models/{revision_id}/offering",
        tags=["Models"],
        summary="모델 판본 제공·설명 갱신",
        description="모델을 소유한 자동화 계정이 판본을 사용자의 적용 대상으로 제공합니다. "
        "설명에는 목적·적용 대상 데이터·한계를 적습니다.",
        response_model=ModelOfferingStateOut,
    )
    def offer_model(revision_id: str, body: ModelOfferingRequest, actor: Actor = MANAGE_MODEL):
        owned_model(revision_id, actor)
        try:
            store.offer_model(
                revision_id,
                description=body.description,
                actor_id=actor.id,
                actor_type=actor.kind,
                owner_actor=None if actor.privileged else actor.id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="모델 판본을 찾을 수 없습니다.") from exc
        except ModelOfferingUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return offering_state(owned_model(revision_id, actor), actor)

    @router.delete(
        "/api/ml/models/{revision_id}/offering",
        tags=["Models"],
        summary="모델 판본 제공 중단",
        description="새 적용만 중단합니다. 사용자가 이미 만든 결과와 제공 기록은 유지합니다.",
        response_model=ModelOfferingStateOut,
    )
    def withdraw_model_offering(revision_id: str, actor: Actor = MANAGE_MODEL):
        owned_model(revision_id, actor)
        try:
            store.withdraw_model_offering(
                revision_id,
                actor_id=actor.id,
                actor_type=actor.kind,
                owner_actor=None if actor.privileged else actor.id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="모델 판본을 찾을 수 없습니다.") from exc
        return offering_state(owned_model(revision_id, actor), actor)

    return router
