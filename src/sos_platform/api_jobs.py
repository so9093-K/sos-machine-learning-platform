"""모든 Job의 공통 수명주기(목록·상세·Attempt·취소) API와 데이터 Job·데이터 탐색 Job의 제출 API.

종류별 기능 API는 제출 명세와 입력 검증만 소유한다. 취소 가능 여부처럼 종류에 따라 다른 규칙은
`Store.request_cancel`이 정한다.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .api_access import ResourceAccess
from .errors import PlatformError
from .pagination import decode_cursor, page_from_rows
from .schemas import ExplorationJobRequest, JobKind, JobOut, JobPageOut, WorkRequest
from .security import Actor, require
from .store import Store
from .store_jobs import FeatureRetirementCancellationConflict, IcebergCancellationConflict

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
READ_JOB_DEP = Depends(require("job.read"))
CREATE_JOB_DEP = Depends(require("job.create"))
CANCEL_JOB_DEP = Depends(require("job.cancel"))
KIND_FILTER = Query(default=None)


def job_router(store: Store, access: ResourceAccess) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/jobs",
        tags=["Jobs"],
        summary="작업 목록 조회",
        description="모든 종류의 Job을 최신순으로 조회합니다. `kind`를 반복 지정하면 그 종류만, "
        "`model_revision_id`를 지정하면 제출 명세에 그 모델 판본 ID를 직접 지정한 Job(Scoring·단일 입력 "
        "점수 계산)만 조회합니다. 다른 결과를 거쳐 모델을 간접 참조하는 Job은 포함하지 않습니다.",
        response_model=JobPageOut,
    )
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        kind: list[JobKind] | None = KIND_FILTER,
        model_revision_id: str | None = Query(default=None, min_length=36, max_length=36),
        actor: Actor = READ_JOB_DEP,
    ):
        rows = store.jobs(
            limit=limit + 1,
            before=decode_cursor(cursor, "Job"),
            actor_id=None if actor.privileged else actor.id,
            kinds=kind,
            model_revision_id=model_revision_id,
        )
        return page_from_rows(rows, limit)

    @router.post(
        "/api/jobs",
        tags=["Jobs"],
        summary="작업 제출",
        description="멱등성·권한 규칙을 확인한 뒤 비동기 Job을 접수합니다. 완료 여부는 상세 조회에서 확인합니다.",
        status_code=202,
        response_model=JobOut,
    )
    def create_job(
        body: WorkRequest,
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
        access.dataset(body.dataset_id, actor)
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind=body.kind,
            dataset_id=body.dataset_id,
            spec=body.model_dump(exclude_none=True),
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    @router.post(
        "/api/explorations/jobs",
        tags=["Data Exploration"],
        summary="데이터 탐색 작업 제출",
        status_code=202,
        response_model=JobOut,
    )
    def create_exploration_job(
        body: ExplorationJobRequest,
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
        access.dataset(body.dataset_id, actor)
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind="exploration",
            dataset_id=body.dataset_id,
            spec=body.model_dump(exclude_none=True),
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    @router.get(
        "/api/jobs/{job_id}",
        tags=["Jobs"],
        summary="작업 상세 조회",
        description="Job의 상태·결과·오류와 모든 Attempt를 조회합니다.",
        response_model=JobOut,
    )
    def get_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return access.job(job_id, actor)

    @router.post(
        "/api/jobs/{job_id}/cancel",
        tags=["Jobs"],
        summary="작업 취소 요청",
        description="취소를 요청하며 실제 작업 중단이나 외부 저장소 변경의 롤백을 보장하지 않습니다. "
        "외부 커밋이 진행 중인 Iceberg 적재와 Feature Service 폐기는 취소할 수 없습니다. 이미 게시된 "
        "결과는 되돌리지 않습니다.",
        response_model=JobOut,
    )
    def cancel(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        access.job(job_id, actor)
        try:
            result = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        except IcebergCancellationConflict as exc:
            raise PlatformError("SOS-ICEBERG-CANCEL-409") from exc
        except FeatureRetirementCancellationConflict as exc:
            raise PlatformError("SOS-FEATURE-RETIRE-CANCEL-409") from exc
        if result is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return result

    return router
