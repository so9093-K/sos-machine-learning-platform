"""Dataset 등록·탐색·이해와 Iceberg 연결·Feature 미리보기 API."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from .api_access import ResourceAccess
from .datasets import (
    connect_iceberg,
    iceberg_snapshots,
    pinned_scan_dataset,
    reserve_iceberg_write_target,
    save_upload_file,
)
from .discovery import columns as discovery_columns
from .discovery import explore as discovery_explore
from .discovery import overview as discovery_overview
from .discovery import preview as discovery_preview
from .discovery import profile as discovery_profile
from .discovery import query_dataset
from .errors import PlatformError
from .file_formats import upload_options_from_mime
from .ingestion import UploadPolicy
from .pagination import decode_cursor, page_from_rows
from .processing import bounded_feature_preview
from .resource_admission import FeaturePreviewBudget, reserve_execution_memory
from .schemas import (
    DatasetColumnsOut,
    DatasetOut,
    DatasetOverviewOut,
    DatasetPageOut,
    DatasetQuery,
    ExplorationOut,
    ExplorationRequest,
    FeaturePreview,
    FeaturePreviewOut,
    IcebergConnect,
    IcebergImport,
    JobOut,
    PreviewOut,
    ProfileOut,
    SnapshotOut,
    UnderstandingOut,
    UnderstandingRequest,
    UploadPolicyOut,
)
from .security import Actor, require
from .store import Store

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
READ_DATASET_DEP = Depends(require("dataset.read"))
CREATE_DATASET_DEP = Depends(require("dataset.create"))
CREATE_JOB_DEP = Depends(require("job.create"))
SNAPSHOT_READ_DEP = Depends(require("snapshot.read"))


def dataset_router(store: Store, access: ResourceAccess, upload_policy: UploadPolicy) -> APIRouter:
    """Dataset 원본과 동기 탐색·변환 미리보기 경로. 긴 계산은 Job으로 제출한다."""
    router = APIRouter()

    @router.get(
        "/api/datasets",
        tags=["Datasets"],
        summary="Dataset 목록 조회",
        response_model=DatasetPageOut,
    )
    def list_dataset_page(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_DATASET_DEP,
    ):
        """Dataset 목록은 키셋 페이지 응답으로 제공한다."""
        before = decode_cursor(cursor, "Dataset")
        rows = store.dataset_page(
            limit=limit + 1,
            before=before,
            actor_id=None if actor.privileged else actor.id,
        )
        return page_from_rows(rows, limit)

    @router.get(
        "/api/datasets/upload-policy",
        tags=["Datasets"],
        summary="Dataset 업로드 제한 조회",
        response_model=UploadPolicyOut,
    )
    def get_upload_policy(actor: Actor = CREATE_DATASET_DEP):
        return upload_policy.describe()

    @router.post(
        "/api/datasets/files",
        tags=["Datasets"],
        summary="파일 Dataset 등록",
        description="원본 데이터 파일을 등록합니다. Dataset Understanding이나 모델 학습은 자동 실행하지 않습니다.",
        status_code=201,
        response_model=DatasetOut,
    )
    async def upload(file: Annotated[UploadFile, File()], actor: Actor = CREATE_DATASET_DEP):
        try:
            read_options = upload_options_from_mime(file.filename or "", file.content_type)
            if file.size is not None and file.size > upload_policy.max_bytes:
                raise PlatformError("SOS-UPLOAD-TOO-LARGE")
            # UploadFile의 스풀 파일을 사용해 전체 bytes 사본을 만들지 않는다.
            return await run_in_threadpool(
                save_upload_file,
                store,
                file.filename or "unknown",
                file.file,
                owner_actor=actor.id,
                max_bytes=upload_policy.max_bytes,
                read_options=read_options,
            )
        except (PlatformError, ValueError):
            raise
        except Exception as exc:
            raise PlatformError("SOS-STORAGE-001") from exc
        finally:
            await file.close()

    @router.get(
        "/api/datasets/{dataset_id}",
        tags=["Datasets"],
        summary="Dataset 상세 조회",
        response_model=DatasetOut,
    )
    def dataset(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return access.dataset(dataset_id, actor)

    @router.get(
        "/api/datasets/{dataset_id}/preview",
        tags=["Data Exploration"],
        summary="Dataset 데이터 미리보기",
        description="일부 행의 미리보기는 전체 Dataset의 대표 표본이나 전체 데이터 통계를 의미하지 않습니다.",
        response_model=PreviewOut,
    )
    def dataset_preview(
        dataset_id: str,
        limit: int = Query(default=30, ge=1, le=100),
        actor: Actor = READ_DATASET_DEP,
    ):
        return discovery_preview(store, access.dataset(dataset_id, actor), limit)

    @router.post(
        "/api/datasets/{dataset_id}/query",
        tags=["Data Exploration"],
        summary="Dataset 조건 조회",
        response_model=PreviewOut,
    )
    def dataset_query(dataset_id: str, body: DatasetQuery, actor: Actor = READ_DATASET_DEP):
        return query_dataset(store, access.dataset(dataset_id, actor), body)

    @router.get(
        "/api/datasets/{dataset_id}/profile",
        tags=["Data Exploration"],
        summary="Dataset 프로파일 조회",
        response_model=ProfileOut,
    )
    def dataset_profile(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return discovery_profile(store, access.dataset(dataset_id, actor), sample=1000)

    @router.get(
        "/api/datasets/{dataset_id}/overview",
        tags=["Data Exploration"],
        summary="Dataset 개요 조회",
        response_model=DatasetOverviewOut,
    )
    def dataset_overview(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return discovery_overview(store, access.dataset(dataset_id, actor))

    @router.get(
        "/api/datasets/{dataset_id}/columns",
        tags=["Data Exploration"],
        summary="Dataset 컬럼 정보 조회",
        response_model=DatasetColumnsOut,
    )
    def dataset_columns(
        dataset_id: str,
        search: str = Query(default="", max_length=120),
        actor: Actor = READ_DATASET_DEP,
    ):
        return discovery_columns(store, access.dataset(dataset_id, actor), search=search)

    @router.get(
        "/api/datasets/{dataset_id}/understanding",
        tags=["Data Exploration"],
        summary="Dataset 이해 결과 조회",
        response_model=UnderstandingOut,
    )
    def dataset_understanding(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        """원본 등록과 독립적으로 계산된 최신 이해 결과 및 실행 상태를 제공한다."""
        item = access.dataset(dataset_id, actor)
        # 완료 트랜잭션의 중간을 읽지 않도록 Job 상태를 먼저 조회한다.
        # 반대 순서에서는 profile=None 조회 후 Worker 커밋, job=Succeeded가 가능하다.
        latest = store.latest_understanding_job(item["id"])
        profile = store.latest_dataset_profile(item["id"], item["version"])
        return {
            "dataset_id": item["id"],
            "version": item["version"],
            "profile": profile,
            "job_status": latest["status"] if latest else None,
            "job_id": latest["id"] if latest else None,
        }

    @router.post(
        "/api/datasets/{dataset_id}/understanding/jobs",
        tags=["Data Exploration"],
        summary="Dataset 이해 작업 제출",
        status_code=202,
        response_model=JobOut,
    )
    def submit_understanding(
        dataset_id: str,
        body: UnderstandingRequest,
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
        access.dataset(dataset_id, actor)
        access.require_submission_key(idempotency_key)
        # 새 Job 종류 없이 기존 Profile Job/Attempt 인프라를 그대로 사용한다.
        return store.create_job(
            kind="profile",
            dataset_id=dataset_id,
            spec={"mode": "understanding", "scope": body.scope},
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    @router.post(
        "/api/datasets/{dataset_id}/explorations",
        tags=["Data Exploration"],
        summary="Dataset 탐색 실행",
        response_model=ExplorationOut,
    )
    def dataset_exploration(
        dataset_id: str, body: ExplorationRequest, actor: Actor = READ_DATASET_DEP
    ):
        return discovery_explore(
            store,
            access.dataset(dataset_id, actor),
            body.column,
            body.metric,
            body.group_by,
            scope="sample",
        )

    @router.post(
        "/api/iceberg/import",
        tags=["Iceberg"],
        summary="Iceberg 적재 작업 제출",
        description="Iceberg 배치 적재 작업을 접수합니다. 외부 Snapshot 커밋은 일반적인 Job 취소로 되돌릴 수 없습니다.",
        status_code=202,
        response_model=JobOut,
    )
    def iceberg_import(
        body: IcebergImport,
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
        try:
            # 실테이블 검사 자체는 부작용 없이 수행한다. 신규 이름 예약은
            # Job의 멱등성 검사 후 같은 PostgreSQL 트랜잭션에서 확정한다.
            reserve_iceberg_write_target(store, body.table_name, actor.id, reserve_if_missing=False)
            return store.create_job(
                kind="iceberg_import",
                dataset_id=body.dataset_id,
                spec=body.model_dump(),
                idempotency_key=idempotency_key,
                request_id=request.state.request_id,
                owner_actor=actor.id,
                actor_type=actor.kind,
                reserve_iceberg_table=body.table_name,
            )
        except PermissionError as exc:
            # 이름·소유권 충돌을 다른 Actor에게 노출하지 않는다.
            raise HTTPException(status_code=404, detail="Iceberg 테이블을 찾을 수 없습니다.") from exc

    def check_table_access(table_name: str, actor: Actor) -> None:
        """테이블명 추측으로 타 사용자 Snapshot을 공개하거나 연결하지 않는다."""
        if not (actor.privileged or actor.can("storage.inspect")):
            if not store.owns_iceberg_table(table_name, actor.id):
                raise HTTPException(status_code=404, detail="Iceberg 테이블을 찾을 수 없습니다.")

    @router.post(
        "/api/datasets/iceberg",
        tags=["Iceberg"],
        summary="Iceberg Dataset 연결",
        status_code=201,
        response_model=DatasetOut,
    )
    def connect_table(body: IcebergConnect, actor: Actor = CREATE_DATASET_DEP):
        check_table_access(body.table_name, actor)
        return connect_iceberg(store, body.table_name, body.snapshot_id, owner_actor=actor.id)

    @router.get(
        "/api/iceberg/{table_name}/snapshots",
        tags=["Iceberg"],
        summary="Iceberg 테이블 Snapshot 조회",
        response_model=list[SnapshotOut],
    )
    def snapshots(table_name: str, actor: Actor = SNAPSHOT_READ_DEP):
        check_table_access(table_name, actor)
        return iceberg_snapshots(store, table_name)

    @router.post(
        "/api/features/preview",
        tags=["Data Preparation"],
        summary="Feature 변환 미리보기",
        response_model=FeaturePreviewOut,
    )
    def feature_preview(body: FeaturePreview, actor: Actor = READ_DATASET_DEP):
        # 입력의 앞 1,000행을 먼저 자르면 정렬·Lag·Rolling 경계가 실제 Job과 달라진다.
        # 전체 입력에서 같은 연산을 수행하고 출력 행 수만 제한한다.
        # 동기 Polars 계산도 Worker·DuckDB와 동일한 논리적 수용 풀을 공유한다.
        # 예약은 원본 캐시 Lease/입력 스캔 이전에 확보해 경합 시 파일·CPU를 잡지 않는다.
        with reserve_execution_memory(store, FeaturePreviewBudget.from_env().memory_mib):
            with pinned_scan_dataset(store, access.dataset(body.dataset_id, actor)) as lf:
                return {"sampled": False, **bounded_feature_preview(lf, body.transform)}

    return router
