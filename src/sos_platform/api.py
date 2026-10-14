"""데이터 탐색과 비동기 처리를 제공하는 공통 플랫폼 API."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference

from .datasets import (
    MAX_UPLOAD,
    connect_iceberg,
    iceberg_snapshots,
    profile,
    safe_preview,
    save_upload,
    scan_dataset,
    transform,
)
from .object_store import ObjectStore
from .schemas import FeaturePreview, IcebergConnect, IcebergImport, WorkRequest
from .store import Store


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
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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

    @app.get("/api/datasets")
    def list_datasets():
        return store.datasets()

    @app.post("/api/datasets/files", status_code=201)
    async def upload(file: Annotated[UploadFile, File()]):
        # Read one extra byte to reject oversized uploads before writing to disk.
        data = await file.read(MAX_UPLOAD + 1)
        try:
            return save_upload(store, file.filename or "unknown", data)
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise HTTPException(status_code=422, detail="Invalid data file") from exc
        finally:
            await file.close()

    @app.get("/api/datasets/{dataset_id}")
    def dataset(dataset_id: str):
        return existing(dataset_id)

    @app.get("/api/datasets/{dataset_id}/preview")
    def dataset_preview(dataset_id: str, limit: int = Query(default=30, ge=1, le=100)):
        return safe_preview(scan_dataset(store, existing(dataset_id)), limit)

    @app.get("/api/datasets/{dataset_id}/profile")
    def dataset_profile(dataset_id: str):
        return profile(scan_dataset(store, existing(dataset_id)), sample=1000)

    @app.post("/api/iceberg/import", status_code=202)
    def iceberg_import(body: IcebergImport):
        existing(body.dataset_id)
        return store.create_job(
            kind="iceberg_import", dataset_id=body.dataset_id, spec=body.model_dump()
        )

    @app.post("/api/datasets/iceberg", status_code=201)
    def connect_table(body: IcebergConnect):
        return connect_iceberg(store, body.table_name, body.snapshot_id)

    @app.get("/api/iceberg/{table_name}/snapshots")
    def snapshots(table_name: str):
        return iceberg_snapshots(store, table_name)

    @app.post("/api/features/preview")
    def feature_preview(body: FeaturePreview):
        lf = scan_dataset(store, existing(body.dataset_id)).head(1000)
        return {"sampled": True, **safe_preview(transform(lf, body.transform), 30)}

    @app.get("/api/jobs")
    def list_jobs():
        return store.jobs()

    @app.post("/api/jobs", status_code=202)
    def create_job(body: WorkRequest):
        existing(body.dataset_id)
        return store.create_job(
            kind=body.kind, dataset_id=body.dataset_id, spec=body.model_dump(exclude_none=True)
        )

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        result = store.get_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        result = store.request_cancel(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    return app


app = create_app()
