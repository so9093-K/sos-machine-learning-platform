"""공개 데이터 가져오기 HTTP 계약. 검증한 원본만 이용 조건 확인 후 Job으로 가져온다."""

from fastapi import APIRouter, Depends, Header, Request

from .api_access import ResourceAccess
from .errors import InvalidRequestError
from .public_datasets import SOURCES, catalog
from .schemas import JobOut, PublicImportRequest, PublicSourceOut
from .security import Actor, require
from .store import Store

READ_DATASET_DEP = Depends(require("dataset.read"))
CREATE_DATASET_DEP = Depends(require("dataset.create"))


def public_dataset_router(store: Store, access: ResourceAccess) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/public-datasets",
        tags=["Datasets"],
        summary="가져올 수 있는 공개 데이터",
        description="검증한 원본(고정 URL·SHA-256)과 이용 조건·인용, 원본에서 만드는 Dataset 부분을 반환합니다.",
        response_model=list[PublicSourceOut],
    )
    def list_public_datasets(actor: Actor = READ_DATASET_DEP):
        return catalog()

    @router.post(
        "/api/public-datasets/{source_id}/imports",
        tags=["Datasets"],
        summary="공개 데이터 가져오기",
        description=(
            "이용 조건 판본을 확인한 사용자가 원본의 한 부분을 Dataset으로 가져옵니다. Worker가 원본을 받아 "
            "고정 SHA-256을 확인하고 주제 입력 형식으로 준비합니다. 202 Accepted는 가져오기 완료가 아닙니다. "
            "성공하면 `result.dataset_id`가 요청자 소유의 새 Dataset이며, 출처는 `result.public_source`에 남습니다."
        ),
        status_code=202,
        response_model=JobOut,
    )
    def create_public_import(
        source_id: str,
        body: PublicImportRequest,
        request: Request,
        actor: Actor = CREATE_DATASET_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        access.require_submission_key(idempotency_key)
        source = SOURCES.get(source_id)
        if source is None or source.part(body.part) is None:
            raise InvalidRequestError("가져올 수 없는 공개 데이터입니다. 목록을 다시 확인하세요.")
        if body.accept_terms_version != source.terms_version:
            raise InvalidRequestError("이용 조건이 바뀌었습니다. 현재 이용 조건을 확인한 뒤 다시 요청하세요.")
        return store.create_job(
            kind="public_import",
            dataset_id=None,
            spec={
                "source_id": source.id, "part": body.part, "url": source.url, "sha256": source.sha256,
                "terms_version": source.terms_version, "recipe_version": source.recipe_version,
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    return router
