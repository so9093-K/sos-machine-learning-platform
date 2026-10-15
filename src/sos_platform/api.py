"""데이터 탐색과 비동기 처리를 제공하는 공통 플랫폼 API."""

import base64
import binascii
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference
from starlette.concurrency import run_in_threadpool

from .api_errors import register_error_handlers
from .config import StoragePolicy
from .datasets import (
    MAX_UPLOAD,
    connect_iceberg,
    iceberg_snapshots,
    save_upload,
    scan_dataset,
)
from .errors import ERRORS
from .examples import list_examples, use_example
from .object_store import ObjectStore
from .observability import emit
from .processing import bounded_feature_preview, profile, safe_preview
from .reconciler import inspect_iceberg_references, inspect_page
from .retention import ensure_tag, snapshot_status
from .schemas import (
    DatasetOut,
    ExampleOut,
    FeaturePreview,
    FeaturePreviewOut,
    IcebergConnect,
    IcebergImport,
    IcebergReferenceOut,
    JobOut,
    JobPageOut,
    PreviewOut,
    ProblemOut,
    ProfileOut,
    SnapshotOut,
    StorageInspectionOut,
    StoragePolicyOut,
    WorkRequest,
)
from .security import Actor, IdentitySettings, current_actor, require, resource_allowed
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


def create_app(home: Path | None = None) -> FastAPI:
    store = Store(home or Path(os.environ.get("SOS_HOME", ".sos")))
    identity = IdentitySettings.from_env()

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://localhost:{os.environ.get('SOS_WEB_PORT', '9601')}",
            f"http://127.0.0.1:{os.environ.get('SOS_WEB_PORT', '9601')}",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
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

    @app.get("/api/auth/me")
    def auth_me(actor: Actor = IDENTITY_DEP):
        return {
            "actor_id": actor.id,
            "actor_type": actor.kind,
            "roles": sorted(actor.roles),
            "mode": identity.mode,
        }

    @app.get("/api/audit")
    def audit_events(
        limit: int = Query(default=50, ge=1, le=100),
        request_id: str | None = Query(default=None, min_length=36, max_length=36),
        job_id: str | None = Query(default=None, min_length=36, max_length=36),
        actor: Actor = AUDIT_DEP,
    ):
        """운영자만 요청 ID 또는 Job ID의 과거 변경·거부 이력을 조회한다."""
        return store.audit_events(limit=limit, request_id=request_id, resource_id=job_id)

    @app.get("/api/diagnostics/jobs/{job_id}")
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

    @app.get("/api/datasets", response_model=list[DatasetOut])
    def list_datasets(actor: Actor = READ_DATASET_DEP):
        return store.datasets(actor_id=None if actor.privileged else actor.id)

    @app.post("/api/datasets/files", status_code=201, response_model=DatasetOut)
    async def upload(file: Annotated[UploadFile, File()], actor: Actor = CREATE_DATASET_DEP):
        # 제한보다 한 바이트 더 읽어 파일 전체를 저장하기 전에 크기 초과를 검출한다.
        data = await file.read(MAX_UPLOAD + 1)
        try:
            # S3·PostgreSQL의 동기 I/O를 ASGI 이벤트 루프에서 실행하지 않는다.
            return await run_in_threadpool(
                save_upload, store, file.filename or "unknown", data, owner_actor=actor.id
            )
        except ValueError:
            raise
        except Exception as exc:
            # 저장소 장애를 사용자 파일의 검증 오류(422)로 잘못 설명하지 않는다.
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SOS-STORAGE-001",
                    "message": ERRORS["SOS-STORAGE-001"].message,
                },
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
        return safe_preview(scan_dataset(store, existing(dataset_id, actor)), limit)

    @app.get("/api/datasets/{dataset_id}/profile", response_model=ProfileOut)
    def dataset_profile(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return profile(scan_dataset(store, existing(dataset_id, actor)), sample=1000)

    @app.post("/api/iceberg/import", status_code=202, response_model=JobOut)
    def iceberg_import(body: IcebergImport, request: Request, actor: Actor = CREATE_JOB_DEP):
        existing(body.dataset_id, actor)
        return store.create_job(
            kind="iceberg_import",
            dataset_id=body.dataset_id,
            spec=body.model_dump(),
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    @app.post("/api/datasets/iceberg", status_code=201, response_model=DatasetOut)
    def connect_table(body: IcebergConnect, actor: Actor = CREATE_DATASET_DEP):
        return connect_iceberg(store, body.table_name, body.snapshot_id, owner_actor=actor.id)

    @app.get("/api/iceberg/{table_name}/snapshots", response_model=list[SnapshotOut])
    def snapshots(table_name: str, actor: Actor = INSPECT_DEP):
        return iceberg_snapshots(store, table_name)

    @app.post("/api/features/preview", response_model=FeaturePreviewOut)
    def feature_preview(body: FeaturePreview, actor: Actor = READ_DATASET_DEP):
        # 입력의 앞 1,000행을 먼저 자르면 정렬·Lag·Rolling 경계가 실제 Job과 달라진다.
        # 전체 입력에서 같은 연산을 수행하고 출력 행 수만 제한한다.
        lf = scan_dataset(store, existing(body.dataset_id, actor))
        return {"sampled": False, **bounded_feature_preview(lf, body.transform)}

    @app.get("/api/jobs", response_model=JobPageOut)
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        before = None
        if cursor:
            try:
                raw = base64.b64decode(
                    cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
                )
                before = json.loads(raw)
                if (
                    not isinstance(before, dict)
                    or set(before) != {"created_at", "id"}
                    or not all(isinstance(before[key], str) and before[key] for key in before)
                    or len(before["created_at"]) > 80
                    or len(before["id"]) != 36
                ):
                    raise ValueError("Invalid Job cursor")
            except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError("Invalid Job cursor") from exc
        rows = store.jobs(
            limit=limit + 1, before=before, actor_id=None if actor.privileged else actor.id
        )
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            token = json.dumps(
                {"created_at": last["created_at"], "id": last["id"]}, separators=(",", ":")
            )
            next_cursor = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
        return {"items": rows, "next_cursor": next_cursor}

    @app.post("/api/jobs", status_code=202, response_model=JobOut)
    def create_job(body: WorkRequest, request: Request, actor: Actor = CREATE_JOB_DEP):
        existing(body.dataset_id, actor)
        return store.create_job(
            kind=body.kind,
            dataset_id=body.dataset_id,
            spec=body.model_dump(exclude_none=True),
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
        )

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return existing_job(job_id, actor)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobOut)
    def cancel(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        existing_job(job_id, actor)
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
