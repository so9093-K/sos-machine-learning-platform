"""데이터 탐색과 비동기 처리를 제공하는 공통 플랫폼 API."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference
from starlette.concurrency import run_in_threadpool
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from .api_errors import problem_response, register_error_handlers
from .config import StoragePolicy
from .datasets import connect_iceberg, iceberg_snapshots, pinned_scan_dataset, save_upload_file
from .discovery import columns as discovery_columns
from .discovery import explore as discovery_explore
from .discovery import overview as discovery_overview
from .discovery import preview as discovery_preview
from .discovery import profile as discovery_profile
from .discovery import query_dataset
from .errors import ERRORS, PlatformError
from .examples import list_examples, use_example
from .ingestion import MULTIPART_OVERHEAD_BYTES, UploadPolicy
from .object_store import ObjectStore
from .observability import emit
from .pagination import decode_cursor, page_from_rows
from .processing import bounded_feature_preview
from .reconciler import inspect_iceberg_references, inspect_page
from .retention import ensure_tag, snapshot_status
from .schemas import (
    AuditEventOut,
    AuthMeOut,
    DatasetColumnsOut,
    DatasetOut,
    DatasetOverviewOut,
    DatasetPageOut,
    DatasetQuery,
    ExampleOut,
    ExplorationJobOut,
    ExplorationJobPageOut,
    ExplorationJobRequest,
    ExplorationOut,
    ExplorationRequest,
    FeaturePreview,
    FeaturePreviewOut,
    IcebergConnect,
    IcebergImport,
    IcebergReferenceOut,
    InstanceSettingsOut,
    InstanceSettingsUpdate,
    JobDiagnosticsOut,
    JobOut,
    JobPageOut,
    PreviewOut,
    ProblemOut,
    ProfileOut,
    SnapshotOut,
    StorageInspectionOut,
    StoragePolicyOut,
    UnderstandingOut,
    UnderstandingRequest,
    UploadPolicyOut,
    WorkRequest,
)
from .security import ACTIONS, Actor, IdentitySettings, current_actor, require, resource_allowed
from .store import IcebergCancellationConflict, Store

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
IDENTITY_DEP = Depends(current_actor)
READ_DATASET_DEP = Depends(require("dataset.read"))
CREATE_DATASET_DEP = Depends(require("dataset.create"))
READ_JOB_DEP = Depends(require("job.read"))
CREATE_JOB_DEP = Depends(require("job.create"))
CANCEL_JOB_DEP = Depends(require("job.cancel"))
INSPECT_DEP = Depends(require("storage.inspect"))
RETAIN_DEP = Depends(require("snapshot.retain"))
AUDIT_DEP = Depends(require("audit.read"))
INSTANCE_CONFIG_DEP = Depends(require("instance.configure"))
SNAPSHOT_READ_DEP = Depends(require("snapshot.read"))


def create_app(home: Path | None = None) -> FastAPI:
    store = Store(home or Path(os.environ.get("SOS_HOME", ".sos")))
    identity = IdentitySettings.from_env()
    upload_policy = UploadPolicy.from_env()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            store.initialize()
            yield
        finally:
            # 테스트·재시작에서 애플리케이션 소유 DB 풀을 남기지 않는다.
            store.engine.dispose()

    app = FastAPI(
        title="SOS Machine Learning Platform",
        version="0.0.1",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        responses={"default": {"model": ProblemOut, "description": "Structured API error"}},
    )
    app.state.identity = identity
    register_error_handlers(app)

    @app.middleware("http")
    async def security_audit(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        emit(
            "api.request.complete",
            service="sos-api",
            request_id=request.state.request_id,
            status=str(response.status_code),
        )
        if response.status_code in (401, 403):
            actor = getattr(request.state, "actor", None)
            try:
                # 거부 요청의 감사 DB 기록도 다른 API 요청을 막아서는 안 된다.
                await run_in_threadpool(
                    store.record_audit,
                    actor_id=actor.id if actor else "anonymous",
                    actor_type=actor.kind if actor else "anonymous",
                    action="security.access_denied",
                    resource_type="api",
                    resource_id=request.url.path[:200],
                    decision="deny",
                    outcome="denied",
                    reason=str(response.status_code),
                    request_id=request.state.request_id,
                )
            except Exception:
                # 접근 거부는 유지하되 감사 저장 장애를 조용히 숨기지는 않는다.
                emit(
                    "audit.denial.record_failed",
                    service="sos-api",
                    level=logging.ERROR,
                    request_id=request.state.request_id,
                    error_code="SOS-API-INTERNAL",
                )
        return response

    # Multipart를 파싱해 디스크에 스풀링하기 전에 본문 전체를 제한한다.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_size=upload_policy.max_bytes + MULTIPART_OVERHEAD_BYTES,
    )

    @app.middleware("http")
    async def normalize_upload_body_limit(request: Request, call_next):
        """본문 제한 미들웨어의 조기 413도 동일한 API 오류 계약으로 반환한다."""
        response = await call_next(request)
        if (
            request.method == "POST"
            and request.url.path == "/api/datasets/files"
            and response.status_code == 413
            and "X-Request-ID" not in response.headers
        ):
            code = "SOS-UPLOAD-TOO-LARGE"
            return problem_response(request, code, 413, ERRORS[code].message)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://localhost:{os.environ.get('SOS_WEB_PORT', '9601')}",
            f"http://127.0.0.1:{os.environ.get('SOS_WEB_PORT', '9601')}",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )

    def check_ownership(actor: Actor, owner: str | None, kind: str, identifier: str) -> None:
        try:
            resource_allowed(actor, owner)
        except HTTPException:
            # 존재 여부를 클라이언트에 노출하지 않는 404 권한 거부는 별도로 감사한다.
            try:
                store.record_audit(
                    actor_id=actor.id,
                    actor_type=actor.kind,
                    action="security.resource_access_denied",
                    resource_type=kind,
                    resource_id=identifier,
                    decision="deny",
                    outcome="denied",
                    reason="ownership",
                )
            except Exception:
                emit(
                    "audit.resource_denial.record_failed",
                    service="sos-api",
                    level=logging.ERROR,
                    error_code="SOS-API-INTERNAL",
                )
            raise

    def existing(dataset_id: str, actor: Actor) -> dict:
        item = store.get_dataset(dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        check_ownership(actor, item.get("owner_actor"), "dataset", dataset_id)
        return item

    def require_job_submission_key(value: str | None) -> None:
        """외부 토큰 API에서 무키 Job 재전송을 기본 거부한다."""
        if identity.mode == "token" and value is None:
            raise PlatformError("SOS-JOB-IDEMPOTENCY-REQUIRED")

    def existing_job(job_id: str, actor: Actor) -> dict:
        item = store.get_job(job_id)
        if not item:
            raise HTTPException(status_code=404, detail="Job not found")
        check_ownership(actor, item.get("owner_actor"), "job", job_id)
        return item

    @app.get("/docs", include_in_schema=False)
    def scalar_docs():
        return get_scalar_api_reference(openapi_url=app.openapi_url, title=app.title)

    @app.get("/api/health")
    def health():
        """프로세스 자체가 HTTP 응답을 제공하는지 확인한다."""
        return {"status": "ok"}

    @app.get("/api/ready")
    def ready():
        """실제 필수 저장소와 실행기가 준비됐을 때만 200을 반환한다."""
        checks = {}
        try:
            store.initialize()
            checks["database"] = "ready"
        except Exception:
            checks["database"] = "unavailable"
        try:
            ObjectStore().ready()
            checks["storage"] = "ready"
        except Exception:
            checks["storage"] = "unavailable"
        try:
            checks["worker"] = "ready" if store.worker_ready() else "unavailable"
        except Exception:
            checks["worker"] = "unavailable"
        result = {
            "status": "ready" if all(v == "ready" for v in checks.values()) else "unavailable",
            "services": checks,
        }
        if result["status"] != "ready":
            return JSONResponse(status_code=503, content=result)
        return result

    @app.get("/api/v2/auth/me", response_model=AuthMeOut)
    def auth_me(actor: Actor = IDENTITY_DEP):
        return {
            "actor_id": actor.id,
            "actor_type": actor.kind,
            "roles": sorted(actor.roles),
            "capabilities": sorted(action for action in ACTIONS if actor.can(action)),
            "mode": identity.mode,
        }

    @app.get("/api/instance", response_model=InstanceSettingsOut)
    def instance_settings(actor: Actor = IDENTITY_DEP):
        """인증된 클라이언트가 동일한 인스턴스 정보를 받는다."""
        return {
            "display_name": store.instance_display_name(),
        }

    @app.put("/api/instance", response_model=InstanceSettingsOut)
    def change_instance_settings(
        body: InstanceSettingsUpdate,
        request: Request,
        actor: Actor = INSTANCE_CONFIG_DEP,
    ):
        store.set_instance_display_name(
            body.display_name,
            actor_id=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
        )
        return {"display_name": body.display_name}

    @app.get("/api/v2/audit", response_model=list[AuditEventOut])
    def audit_events(
        limit: int = Query(default=50, ge=1, le=100),
        request_id: str | None = Query(default=None, min_length=36, max_length=36),
        job_id: str | None = Query(default=None, min_length=36, max_length=36),
        actor: Actor = AUDIT_DEP,
    ):
        """운영자만 요청 ID 또는 Job ID의 과거 변경·거부 이력을 조회한다."""
        return store.audit_events(limit=limit, request_id=request_id, resource_id=job_id)

    @app.get("/api/v2/diagnostics/jobs/{job_id}", response_model=JobDiagnosticsOut)
    def job_diagnostics(job_id: str, actor: Actor = AUDIT_DEP):
        """권한을 확인한 운영자에게만 정제된 원인 유형·실행 단계를 제공한다."""
        item = store.get_job(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "job_id": item["id"],
            "request_id": item.get("request_id"),
            "status": item["status"],
            "error_code": item.get("error_code"),
            "failure_stage": item.get("failure_stage"),
            "failure_category": item.get("failure_category"),
            "exception_type": item.get("exception_type"),
            "attempts": [
                {
                    "id": attempt["id"],
                    "status": attempt["status"],
                    "error_code": attempt.get("error_code"),
                    "failure_stage": attempt.get("failure_stage"),
                    "failure_category": attempt.get("failure_category"),
                    "exception_type": attempt.get("exception_type"),
                }
                for attempt in item["attempts"]
            ],
        }

    @app.get("/api/storage/policy", response_model=StoragePolicyOut)
    def storage_policy(actor: Actor = INSPECT_DEP):
        """연결 비밀을 제외한 유효 정책과 출처를 확인한다."""
        return StoragePolicy.from_env().describe()

    @app.get("/api/storage/inspection", response_model=StorageInspectionOut)
    def storage_inspection(
        cursor: str | None = Query(default=None, max_length=4096),
        actor: Actor = INSPECT_DEP,
    ):
        """S3 객체를 읽기 전용으로 대조한다. 실제 삭제는 제공하지 않는다."""
        try:
            return inspect_page(store, ObjectStore(), StoragePolicy.from_env(), cursor)
        except ValueError:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Storage inventory is unavailable; no objects were deleted"
            ) from exc

    @app.get("/api/storage/iceberg-references", response_model=list[IcebergReferenceOut])
    def storage_iceberg_references(
        limit: int = Query(default=50, ge=1, le=100),
        actor: Actor = INSPECT_DEP,
    ):
        """등록된 Dataset이 참조하는 Iceberg Snapshot 존재 여부를 확인한다."""
        return inspect_iceberg_references(store, limit=limit)

    @app.post("/api/storage/iceberg-references/{dataset_id}/retain")
    def retain_iceberg_reference(dataset_id: str, actor: Actor = RETAIN_DEP):
        """기존 Dataset을 재확인하고 SOS 영구 보존 Tag를 생성한다."""
        item = store.get_dataset(dataset_id)
        if item is None or item["kind"] != "iceberg":
            raise HTTPException(status_code=404, detail="Iceberg Dataset not found")
        try:
            ensure_tag(
                store,
                dataset_id=dataset_id,
                location=item["location"],
                snapshot_id=item["version"],
            )
            store.confirm_existing_retention(dataset_id, actor_id=actor.id, actor_type=actor.kind)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Snapshot protection could not be verified"
            ) from exc
        status, reason = snapshot_status(store, item)
        return {"dataset_id": dataset_id, "status": status, "reason": reason}

    @app.get("/api/examples", response_model=list[ExampleOut])
    def available_examples(actor: Actor = READ_DATASET_DEP):
        """미리 보기 가능한 합성 데이터 예제의 정의를 조회한다."""
        return list_examples()

    @app.post("/api/examples/{example_id}/use", status_code=201, response_model=DatasetOut)
    def start_example(example_id: str, actor: Actor = CREATE_DATASET_DEP):
        """예제를 명시적으로 선택했을 때만 일반 Dataset으로 등록한다."""
        try:
            return use_example(store, example_id, owner_actor=actor.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Example not found") from exc

    @app.get("/api/datasets", response_model=DatasetPageOut)
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

    @app.get("/api/datasets/upload-policy", response_model=UploadPolicyOut)
    def get_upload_policy(actor: Actor = CREATE_DATASET_DEP):
        return upload_policy.describe()

    @app.post("/api/datasets/files", status_code=201, response_model=DatasetOut)
    async def upload(file: Annotated[UploadFile, File()], actor: Actor = CREATE_DATASET_DEP):
        try:
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
            )
        except (PlatformError, ValueError):
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "SOS-STORAGE-001", "message": ERRORS["SOS-STORAGE-001"].message},
            ) from exc
        finally:
            await file.close()

    @app.get("/api/datasets/{dataset_id}", response_model=DatasetOut)
    def dataset(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return existing(dataset_id, actor)

    @app.get("/api/datasets/{dataset_id}/preview", response_model=PreviewOut)
    def dataset_preview(
        dataset_id: str,
        limit: int = Query(default=30, ge=1, le=100),
        actor: Actor = READ_DATASET_DEP,
    ):
        return discovery_preview(store, existing(dataset_id, actor), limit)

    @app.post("/api/datasets/{dataset_id}/query", response_model=PreviewOut)
    def dataset_query(dataset_id: str, body: DatasetQuery, actor: Actor = READ_DATASET_DEP):
        return query_dataset(store, existing(dataset_id, actor), body)

    @app.get("/api/datasets/{dataset_id}/profile", response_model=ProfileOut)
    def dataset_profile(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return discovery_profile(store, existing(dataset_id, actor), sample=1000)

    @app.get("/api/datasets/{dataset_id}/overview", response_model=DatasetOverviewOut)
    def dataset_overview(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return discovery_overview(store, existing(dataset_id, actor))

    @app.get("/api/datasets/{dataset_id}/columns", response_model=DatasetColumnsOut)
    def dataset_columns(
        dataset_id: str,
        search: str = Query(default="", max_length=120),
        actor: Actor = READ_DATASET_DEP,
    ):
        return discovery_columns(store, existing(dataset_id, actor), search=search)

    @app.get("/api/datasets/{dataset_id}/understanding", response_model=UnderstandingOut)
    def dataset_understanding(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        """원본 등록과 독립적으로 계산된 최신 이해 결과 및 실행 상태를 제공한다."""
        item = existing(dataset_id, actor)
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

    @app.post(
        "/api/datasets/{dataset_id}/understanding/jobs",
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
        existing(dataset_id, actor)
        require_job_submission_key(idempotency_key)
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

    @app.post("/api/datasets/{dataset_id}/explorations", response_model=ExplorationOut)
    def dataset_exploration(
        dataset_id: str, body: ExplorationRequest, actor: Actor = READ_DATASET_DEP
    ):
        return discovery_explore(
            store,
            existing(dataset_id, actor),
            body.column,
            body.metric,
            body.group_by,
            scope="sample",
        )

    @app.post("/api/iceberg/import", status_code=202, response_model=JobOut)
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
        existing(body.dataset_id, actor)
        require_job_submission_key(idempotency_key)
        return store.create_job(
            kind="iceberg_import",
            dataset_id=body.dataset_id,
            spec=body.model_dump(),
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    def check_table_access(table_name: str, actor: Actor) -> None:
        """테이블명 추측으로 타 사용자 Snapshot을 공개하거나 연결하지 않는다."""
        if not (actor.privileged or actor.can("storage.inspect")):
            if not store.owns_iceberg_table(table_name, actor.id):
                raise HTTPException(status_code=404, detail="Iceberg table not found")

    @app.post("/api/datasets/iceberg", status_code=201, response_model=DatasetOut)
    def connect_table(body: IcebergConnect, actor: Actor = CREATE_DATASET_DEP):
        check_table_access(body.table_name, actor)
        return connect_iceberg(store, body.table_name, body.snapshot_id, owner_actor=actor.id)

    @app.get("/api/iceberg/{table_name}/snapshots", response_model=list[SnapshotOut])
    def snapshots(table_name: str, actor: Actor = SNAPSHOT_READ_DEP):
        check_table_access(table_name, actor)
        return iceberg_snapshots(store, table_name)

    @app.post("/api/features/preview", response_model=FeaturePreviewOut)
    def feature_preview(body: FeaturePreview, actor: Actor = READ_DATASET_DEP):
        # 입력의 앞 1,000행을 먼저 자르면 정렬·Lag·Rolling 경계가 실제 Job과 달라진다.
        # 전체 입력에서 같은 연산을 수행하고 출력 행 수만 제한한다.
        with pinned_scan_dataset(store, existing(body.dataset_id, actor)) as lf:
            return {"sampled": False, **bounded_feature_preview(lf, body.transform)}

    def job_page(limit: int, cursor: str | None, actor: Actor, *, discovery_only: bool) -> dict:
        before = decode_cursor(cursor, "Job")
        rows = store.jobs(
            limit=limit + 1,
            before=before,
            actor_id=None if actor.privileged else actor.id,
            kind="exploration" if discovery_only else None,
            exclude_kind=None if discovery_only else "exploration",
        )
        return page_from_rows(rows, limit)

    @app.get("/api/jobs", response_model=JobPageOut)
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return job_page(limit, cursor, actor, discovery_only=False)

    @app.get("/api/explorations/jobs", response_model=ExplorationJobPageOut)
    def list_exploration_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return job_page(limit, cursor, actor, discovery_only=True)

    @app.post("/api/jobs", status_code=202, response_model=JobOut)
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
        existing(body.dataset_id, actor)
        require_job_submission_key(idempotency_key)
        return store.create_job(
            kind=body.kind,
            dataset_id=body.dataset_id,
            spec=body.model_dump(exclude_none=True),
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    @app.post("/api/explorations/jobs", status_code=202, response_model=ExplorationJobOut)
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
        existing(body.dataset_id, actor)
        require_job_submission_key(idempotency_key)
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
        item = existing_job(job_id, actor)
        if item["kind"] != "exploration":
            raise HTTPException(status_code=404, detail="Job not found")
        return item

    @app.get("/api/explorations/jobs/{job_id}", response_model=ExplorationJobOut)
    def get_exploration_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return exploration_job(job_id, actor)

    @app.post("/api/explorations/jobs/{job_id}/cancel", response_model=ExplorationJobOut)
    def cancel_exploration_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        exploration_job(job_id, actor)
        result = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, actor: Actor = READ_JOB_DEP):
        item = existing_job(job_id, actor)
        if item["kind"] == "exploration":
            raise HTTPException(status_code=404, detail="Job not found")
        return item

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobOut)
    def cancel(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        item = existing_job(job_id, actor)
        if item["kind"] == "exploration":
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        except IcebergCancellationConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "SOS-ICEBERG-CANCEL-409", "message": str(exc)},
            ) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    return app


app = create_app()
