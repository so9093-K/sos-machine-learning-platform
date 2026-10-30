"""일반 데이터 Job과 데이터 탐색 Job의 제출·조회·취소 API."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .api_access import ResourceAccess
from .errors import PlatformError
from .pagination import decode_cursor, page_from_rows
from .schemas import (
    ExplorationJobOut,
    ExplorationJobPageOut,
    ExplorationJobRequest,
    JobOut,
    JobPageOut,
    WorkRequest,
)
from .security import Actor, require
from .store import Store
from .store_jobs import FeatureRetirementCancellationConflict, IcebergCancellationConflict

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
READ_JOB_DEP = Depends(require("job.read"))
CREATE_JOB_DEP = Depends(require("job.create"))
CANCEL_JOB_DEP = Depends(require("job.cancel"))


def job_router(store: Store, access: ResourceAccess) -> APIRouter:
    """ML·Workflow 전용 Job은 각 도메인 경로에서만 조회·취소한다."""
    router = APIRouter()

    def job_page(limit: int, cursor: str | None, actor: Actor, *, discovery_only: bool) -> dict:
        before = decode_cursor(cursor, "Job")
        rows = store.jobs(
            limit=limit + 1,
            before=before,
            actor_id=None if actor.privileged else actor.id,
            kind="exploration" if discovery_only else None,
            exclude_kind=None if discovery_only else "exploration",
            exclude_ml_train=not discovery_only,
        )
        return page_from_rows(rows, limit)

    @router.get("/api/jobs", tags=["Jobs"], summary="작업 목록 조회", response_model=JobPageOut)
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return job_page(limit, cursor, actor, discovery_only=False)

    @router.get(
        "/api/explorations/jobs",
        tags=["Data Exploration"],
        summary="데이터 탐색 작업 목록 조회",
        response_model=ExplorationJobPageOut,
    )
    def list_exploration_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return job_page(limit, cursor, actor, discovery_only=True)

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
        response_model=ExplorationJobOut,
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

    def exploration_job(job_id: str, actor: Actor) -> dict:
        item = access.job(job_id, actor)
        if item["kind"] != "exploration":
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return item

    @router.get(
        "/api/explorations/jobs/{job_id}",
        tags=["Data Exploration"],
        summary="데이터 탐색 작업 상세 조회",
        response_model=ExplorationJobOut,
    )
    def get_exploration_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return exploration_job(job_id, actor)

    @router.post(
        "/api/explorations/jobs/{job_id}/cancel",
        tags=["Data Exploration"],
        summary="데이터 탐색 작업 취소 요청",
        response_model=ExplorationJobOut,
    )
    def cancel_exploration_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        exploration_job(job_id, actor)
        result = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if result is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return result

    @router.get("/api/jobs/{job_id}", tags=["Jobs"], summary="작업 상세 조회", response_model=JobOut)
    def get_job(job_id: str, actor: Actor = READ_JOB_DEP):
        item = access.job(job_id, actor)
        if item["kind"] in (
            "exploration",
            "ml_train",
            "ml_score",
            "ml_policy",
            "ml_evaluate",
            "ml_explain",
        ):
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return item

    @router.post(
        "/api/jobs/{job_id}/cancel",
        tags=["Jobs"],
        summary="작업 취소 요청",
        description="취소를 요청하며 실제 작업 중단이나 외부 저장소 변경의 롤백을 보장하지 않습니다.",
        response_model=JobOut,
    )
    def cancel(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        item = access.job(job_id, actor)
        if item["kind"] in (
            "exploration",
            "ml_train",
            "ml_score",
            "ml_policy",
            "ml_evaluate",
            "ml_explain",
        ):
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
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
