"""데이터 탐색과 비동기 처리를 제공하는 공통 플랫폼 API."""

import base64
import binascii
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference

from .config import StoragePolicy
from .datasets import (
    MAX_UPLOAD,
    connect_iceberg,
    iceberg_snapshots,
    save_upload,
    scan_dataset,
)
from .examples import list_examples, use_example
from .object_store import ObjectStore
from .processing import bounded_feature_preview, profile, safe_preview
from .reconciler import inspect_iceberg_references, inspect_page
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
    ProfileOut,
    SnapshotOut,
    StorageInspectionOut,
    StoragePolicyOut,
    WorkRequest,
)
from .store import IcebergCancellationConflict, Store


def create_app(home: Path | None = None) -> FastAPI:
    store = Store(home or Path(os.environ.get("SOS_HOME", ".sos")))

    @asynccontextmanager
    async def lifespan(_app):
        store.initialize()
        yield

    app = FastAPI(
        title="SOS Machine Learning Platform",
        version="0.0.1",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://localhost:{os.environ.get('SOS_WEB_PORT', '9601')}",
            f"http://127.0.0.1:{os.environ.get('SOS_WEB_PORT', '9601')}",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(ValueError)
    async def bad_input(_request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def existing(dataset_id: str) -> dict:
        item = store.get_dataset(dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
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

    @app.get("/api/storage/policy", response_model=StoragePolicyOut)
    def storage_policy():
        """연결 비밀을 제외한 유효 정책과 출처를 확인한다."""
        return StoragePolicy.from_env().describe()

    @app.get("/api/storage/inspection", response_model=StorageInspectionOut)
    def storage_inspection(cursor: str | None = Query(default=None, max_length=4096)):
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
    def storage_iceberg_references(limit: int = Query(default=50, ge=1, le=100)):
        """등록된 Dataset이 참조하는 Iceberg Snapshot 존재 여부를 확인한다."""
        return inspect_iceberg_references(store, limit=limit)

    @app.get("/api/examples", response_model=list[ExampleOut])
    def available_examples():
        """미리 보기 가능한 합성 데이터 예제의 정의를 조회한다."""
        return list_examples()

    @app.post("/api/examples/{example_id}/use", status_code=201, response_model=DatasetOut)
    def start_example(example_id: str):
        """예제를 명시적으로 선택했을 때만 일반 Dataset으로 등록한다."""
        try:
            return use_example(store, example_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Example not found") from exc

    @app.get("/api/datasets", response_model=list[DatasetOut])
    def list_datasets():
        return store.datasets()

    @app.post("/api/datasets/files", status_code=201, response_model=DatasetOut)
    async def upload(file: Annotated[UploadFile, File()]):
        # 제한보다 한 바이트 더 읽어 파일 전체를 저장하기 전에 크기 초과를 검출한다.
        data = await file.read(MAX_UPLOAD + 1)
        try:
            return save_upload(store, file.filename or "unknown", data)
        except ValueError:
            raise
        except Exception as exc:
            # 저장소 장애를 사용자 파일의 검증 오류(422)로 잘못 설명하지 않는다.
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SOS-STORAGE-001",
                    "message": "데이터 저장소를 사용할 수 없습니다. 잠시 후 재시도하세요.",
                },
            ) from exc
        finally:
            await file.close()

    @app.get("/api/datasets/{dataset_id}", response_model=DatasetOut)
    def dataset(dataset_id: str):
        return existing(dataset_id)

    @app.get("/api/datasets/{dataset_id}/preview", response_model=PreviewOut)
    def dataset_preview(dataset_id: str, limit: int = Query(default=30, ge=1, le=100)):
        return safe_preview(scan_dataset(store, existing(dataset_id)), limit)

    @app.get("/api/datasets/{dataset_id}/profile", response_model=ProfileOut)
    def dataset_profile(dataset_id: str):
        return profile(scan_dataset(store, existing(dataset_id)), sample=1000)

    @app.post("/api/iceberg/import", status_code=202, response_model=JobOut)
    def iceberg_import(body: IcebergImport):
        existing(body.dataset_id)
        return store.create_job(
            kind="iceberg_import", dataset_id=body.dataset_id, spec=body.model_dump()
        )

    @app.post("/api/datasets/iceberg", status_code=201, response_model=DatasetOut)
    def connect_table(body: IcebergConnect):
        return connect_iceberg(store, body.table_name, body.snapshot_id)

    @app.get("/api/iceberg/{table_name}/snapshots", response_model=list[SnapshotOut])
    def snapshots(table_name: str):
        return iceberg_snapshots(store, table_name)

    @app.post("/api/features/preview", response_model=FeaturePreviewOut)
    def feature_preview(body: FeaturePreview):
        # 입력의 앞 1,000행을 먼저 자르면 정렬·Lag·Rolling 경계가 실제 Job과 달라진다.
        # 전체 입력에서 같은 연산을 수행하고 출력 행 수만 제한한다.
        lf = scan_dataset(store, existing(body.dataset_id))
        return {"sampled": False, **bounded_feature_preview(lf, body.transform)}

    @app.get("/api/jobs", response_model=JobPageOut)
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
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
        rows = store.jobs(limit=limit + 1, before=before)
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
    def create_job(body: WorkRequest):
        existing(body.dataset_id)
        return store.create_job(
            kind=body.kind, dataset_id=body.dataset_id, spec=body.model_dump(exclude_none=True)
        )

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str):
        result = store.get_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobOut)
    def cancel(job_id: str):
        try:
            result = store.request_cancel(job_id)
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
