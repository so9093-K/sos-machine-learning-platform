"""데이터 탐색과 비동기 처리를 제공하는 공통 플랫폼 API."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import AgentScalarConfig, get_scalar_api_reference
from starlette.concurrency import run_in_threadpool
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from .api_control import control_router
from .api_errors import problem_response, register_error_handlers
from .api_ml_training import training_router
from .api_pipelines import pipeline_router
from .scoring_observations import scoring_observations_router
from .config import StoragePolicy
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
from .errors import ERRORS, PlatformError, RuntimeConfigurationError
from .examples import list_examples, use_example
from .file_formats import upload_options_from_mime
from .ingestion import MULTIPART_OVERHEAD_BYTES, UploadPolicy
from .object_store import ObjectStore
from .observability import emit
from .mlflow_config import MLflowSettings
from .mlflow_errors import MLflowDeliveryError
from .mlflow_read import read_experiments, read_runs, read_model_versions
from .mlflow_registry import verify_registered_version
from .mlflow_tracking import MLflowClient
from .pagination import decode_cursor, page_from_rows
from .processing import bounded_feature_preview
from .reconciler import inspect_iceberg_references, inspect_page
from .resource_admission import FeaturePreviewBudget, reserve_execution_memory
from .retention import ensure_tag, snapshot_status
from .schemas import (
    DatasetColumnsOut,
    DatasetOut,
    DatasetOverviewOut,
    DatasetPageOut,
    DatasetQuery,
    DecisionPolicyRevisionOut,
    EvaluationRevisionOut,
    ExampleOut,
    ExplanationResultOut,
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
    JobOut,
    JobPageOut,
    MLEvaluationJobOut,
    MLEvaluationJobPageOut,
    MLEvaluationRequest,
    MLExplanationJobOut,
    MLExplanationJobPageOut,
    MLExplanationRequest,
    MLflowExportStatusOut,
    MLflowExperimentPageOut,
    MLflowRunPageOut,
    MLflowModelVersionPageOut,
    MLPolicyJobOut,
    MLPolicyJobPageOut,
    MLPolicyRequest,
    MLScoringJobOut,
    MLScoringJobPageOut,
    MLScoringRequest,
    ModelRevisionOut,
    PreviewOut,
    ProblemOut,
    ProfileOut,
    ScoringResultOut,
    SnapshotOut,
    StorageInspectionOut,
    StoragePolicyOut,
    UnderstandingOut,
    UnderstandingRequest,
    UploadPolicyOut,
    WorkRequest,
)
from .security import Actor, IdentitySettings, require, resource_allowed
from .store import Store
from .store_jobs import IcebergCancellationConflict

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
READ_DATASET_DEP = Depends(require("dataset.read"))
CREATE_DATASET_DEP = Depends(require("dataset.create"))
READ_JOB_DEP = Depends(require("job.read"))
CREATE_JOB_DEP = Depends(require("job.create"))
CANCEL_JOB_DEP = Depends(require("job.cancel"))
INSPECT_DEP = Depends(require("storage.inspect"))
RETAIN_DEP = Depends(require("snapshot.retain"))
SNAPSHOT_READ_DEP = Depends(require("snapshot.read"))


# OpenAPI는 API 계약의 표현이며 Scalar는 이를 읽는 문서 UI일 뿐이다.
# 현재 지원하는 기능만 설명하고, 문서 분류가 Runtime이나 Router 경계를 결정하지 않는다.
API_DESCRIPTION = """\
SOS는 데이터 이해·준비, 독립 ML 작업, 등록형 Pipeline과 결과 조사를 연결하는
**API-first 머신러닝 플랫폼**입니다. Web UI와 외부 클라이언트는 같은 API 계약을 사용합니다.

### 시작하기

1. `GET /api/health`로 API 프로세스 응답을 확인하고, `GET /api/ready`로 저장소·Worker 준비 상태를 확인합니다.
2. Token 모드에서는 `Authorization: Bearer <TOKEN>`을 전달하고 `GET /api/v2/auth/me`로 권한을 확인합니다.
3. `GET /api/datasets` 또는 `GET /api/pipelines`로 현재 이용 가능한 데이터와 분석 방법을 살펴봅니다.
4. 데이터를 등록하거나 Job을 제출하려면 해당 Actor 권한이 필요합니다. 변경·실행 API는 **실제 데이터를 생성하거나 계산을 시작**할 수 있습니다.

```bash
curl http://localhost:9602/api/health
curl -H "Authorization: Bearer <TOKEN>" http://localhost:9602/api/v2/auth/me
```

### 실행과 결과

- Job·Pipeline 제출의 **202 Accepted는 완료가 아니라 접수**입니다. 조회 API로 실제 상태와 결과를 확인하세요.
- Token 모드의 Job 제출에는 일반적으로 `Idempotency-Key`가 필요합니다. 같은 의도의 중복 제출을 방지합니다.
- 취소 **요청**과 실제 중단·결과 확정은 다릅니다. 모델 학습 성공과 MLflow Registry 등록도 별도 상태입니다.
- 데이터 미리보기·표본 분석·전체 분석의 범위와, 모델 점수·정책·평가의 의미를 구별해야 합니다.
- 예제 확인은 등록·학습을 자동 실행하지 않습니다. 합성 예제의 이상 점수는 실제 사건이나 탐지 정확도의 근거가 아닙니다.

현재 지원되는 등록형 모델 실험 주제는 활동 이상 탐지(Isolation Forest)입니다.
온라인 Serving, 임의 사용자 코드 실행 및 범용 모델 플러그인은 이 API의 현재 지원 범위가 아닙니다.

공개 계약은 [OpenAPI JSON](/openapi.json)이며, 오류 응답에는 분류·코드·요청 ID가 포함됩니다.
"""

OPENAPI_TAGS = [
    {"name": "System", "description": "프로세스 생존 여부 및 필수 서비스의 준비 상태"},
    {"name": "Identity", "description": "인증 신원, 권한 및 인스턴스 표시 설정"},
    {"name": "Datasets", "description": "데이터 파일·합성 예제·Dataset 판본 등록과 조회"},
    {"name": "Data Exploration", "description": "Dataset 구조·프로파일·데이터 조회 및 탐색 실행"},
    {"name": "Data Preparation", "description": "Feature 변환과 분석용 입력 데이터 준비"},
    {"name": "Iceberg", "description": "Iceberg 데이터 적재·연결 및 Snapshot 조회"},
    {"name": "Experiments", "description": "MLflow Experiment·Run 조회 및 외부 기록 상태"},
    {"name": "Training", "description": "독립 ML 학습·복제·취소 및 작업 이력"},
    {"name": "Models", "description": "MLflow Registry와 SOS의 불변 모델 실행 참조"},
    {"name": "Scoring", "description": "학습된 모델을 이용한 독립 배치 점수화와 결과 조회"},
    {"name": "Decision Policies", "description": "점수에 적용할 판정 기준의 생성·적용·조회"},
    {"name": "Evaluations", "description": "모델 점수와 정책에 대한 평가 실행·결과 조회"},
    {"name": "Explanations", "description": "선택한 점수 결과에 대한 설명 계산과 판본 조회"},
    {"name": "Pipeline Catalog", "description": "등록된 Pipeline의 방법·입력 적합성 조회"},
    {"name": "Pipeline Runs", "description": "Pipeline 실행·진행 상태·결과 후보 조회"},
    {"name": "Jobs", "description": "공통 비동기 작업의 제출·조회·취소"},
    {"name": "Storage", "description": "스토리지 보존 정책과 객체 상태 점검"},
    {"name": "Audit & Diagnostics", "description": "감사 이벤트와 운영자용 작업 진단"},
]

# Scalar의 탐색용 확장. API 기능 분류 자체는 표준 OpenAPI tags에 남는다.
OPENAPI_TAG_GROUPS = [
    {"name": "Platform", "tags": ["System","Identity"]},
    {"name": "Data", "tags": ["Datasets","Data Exploration","Data Preparation","Iceberg"]},
    {"name": "Research & ML", "tags": ["Experiments","Training","Models","Scoring","Decision Policies","Evaluations","Explanations"]},
    {"name": "Pipelines", "tags": ["Pipeline Catalog","Pipeline Runs"]},
    {"name": "Operations", "tags": ["Jobs","Storage","Audit & Diagnostics"]},
]

def create_app(home: Path | None = None, *, identity: IdentitySettings | None = None) -> FastAPI:
    """인증 설정은 실행 시 검증하고, 명세 생성에는 명시적 신원을 사용할 수 있다."""
    # 잘못된 인증 설정은 저장소·로컬 디렉터리를 만들기 전에 거부한다.
    identity = identity if identity is not None else IdentitySettings.from_env()
    store = Store(home or Path(os.environ.get("SOS_HOME", ".sos")))
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
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
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
        allow_methods=["GET", "POST", "PUT"],
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
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
            agent=AgentScalarConfig(disabled=True),
            telemetry=False,
        )

    app.include_router(control_router(store, identity))
    app.include_router(pipeline_router(store, identity_mode=identity.mode))
    app.include_router(scoring_observations_router(store, check_ownership))

    @app.get(
        "/api/storage/policy",
        tags=["Storage"],
        summary="저장소 보존 정책 조회",
        response_model=StoragePolicyOut,
    )
    def storage_policy(actor: Actor = INSPECT_DEP):
        """연결 비밀을 제외한 유효 정책과 출처를 확인한다."""
        return StoragePolicy.from_env().describe()

    @app.get(
        "/api/storage/inspection",
        tags=["Storage"],
        summary="저장소 객체 점검 결과 조회",
        description="저장소의 객체 상태를 점검합니다. 점검은 실제 객체 삭제를 수행하지 않습니다.",
        response_model=StorageInspectionOut,
    )
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

    @app.get(
        "/api/storage/iceberg-references",
        tags=["Iceberg"],
        summary="Iceberg 참조 보존 상태 조회",
        response_model=list[IcebergReferenceOut],
    )
    def storage_iceberg_references(
        limit: int = Query(default=50, ge=1, le=100),
        actor: Actor = INSPECT_DEP,
    ):
        """등록된 Dataset이 참조하는 Iceberg Snapshot 존재 여부를 확인한다."""
        return inspect_iceberg_references(store, limit=limit)

    @app.post(
        "/api/storage/iceberg-references/{dataset_id}/retain",
        tags=["Iceberg"],
        summary="Iceberg 참조 보존 요청",
    )
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
        except Exception as exc:
            # 외부 Catalog에 Tag 쓰기가 이미 성공했을 수 있다. 원문을 공개하거나
            # 안전한 재시도를 암시하지 않고 수동 확인이 필요한 상태로 표현한다.
            raise PlatformError("SOS-ICEBERG-RETAIN") from exc
        status, reason = snapshot_status(store, item)
        return {"dataset_id": dataset_id, "status": status, "reason": reason}

    @app.get(
        "/api/examples",
        tags=["Datasets"],
        summary="선택 가능한 합성 예제 조회",
        response_model=list[ExampleOut],
    )
    def available_examples(actor: Actor = READ_DATASET_DEP):
        """미리 보기 가능한 합성 데이터 예제의 정의를 조회한다."""
        return list_examples()

    @app.post(
        "/api/examples/{example_id}/use",
        tags=["Datasets"],
        summary="합성 예제 Dataset 등록 또는 재사용",
        description="사용자가 명시적으로 요청한 경우에만 합성 예제를 Dataset으로 등록하거나 동일 판본을 재사용합니다.",
        status_code=201,
        response_model=DatasetOut,
    )
    def start_example(example_id: str, actor: Actor = CREATE_DATASET_DEP):
        """예제를 명시적으로 선택했을 때만 일반 Dataset으로 등록한다."""
        try:
            return use_example(store, example_id, owner_actor=actor.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Example not found") from exc

    @app.get(
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

    @app.get(
        "/api/datasets/upload-policy",
        tags=["Datasets"],
        summary="Dataset 업로드 제한 조회",
        response_model=UploadPolicyOut,
    )
    def get_upload_policy(actor: Actor = CREATE_DATASET_DEP):
        return upload_policy.describe()

    @app.post(
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

    @app.get(
        "/api/datasets/{dataset_id}",
        tags=["Datasets"],
        summary="Dataset 상세 조회",
        response_model=DatasetOut,
    )
    def dataset(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return existing(dataset_id, actor)

    @app.get(
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
        return discovery_preview(store, existing(dataset_id, actor), limit)

    @app.post(
        "/api/datasets/{dataset_id}/query",
        tags=["Data Exploration"],
        summary="Dataset 조건 조회",
        response_model=PreviewOut,
    )
    def dataset_query(dataset_id: str, body: DatasetQuery, actor: Actor = READ_DATASET_DEP):
        return query_dataset(store, existing(dataset_id, actor), body)

    @app.get(
        "/api/datasets/{dataset_id}/profile",
        tags=["Data Exploration"],
        summary="Dataset 프로파일 조회",
        response_model=ProfileOut,
    )
    def dataset_profile(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return discovery_profile(store, existing(dataset_id, actor), sample=1000)

    @app.get(
        "/api/datasets/{dataset_id}/overview",
        tags=["Data Exploration"],
        summary="Dataset 개요 조회",
        response_model=DatasetOverviewOut,
    )
    def dataset_overview(dataset_id: str, actor: Actor = READ_DATASET_DEP):
        return discovery_overview(store, existing(dataset_id, actor))

    @app.get(
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
        return discovery_columns(store, existing(dataset_id, actor), search=search)

    @app.get(
        "/api/datasets/{dataset_id}/understanding",
        tags=["Data Exploration"],
        summary="Dataset 이해 결과 조회",
        response_model=UnderstandingOut,
    )
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

    @app.post(
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
            existing(dataset_id, actor),
            body.column,
            body.metric,
            body.group_by,
            scope="sample",
        )

    @app.post(
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
        existing(body.dataset_id, actor)
        require_job_submission_key(idempotency_key)
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
            raise HTTPException(status_code=404, detail="Iceberg table not found") from exc

    def check_table_access(table_name: str, actor: Actor) -> None:
        """테이블명 추측으로 타 사용자 Snapshot을 공개하거나 연결하지 않는다."""
        if not (actor.privileged or actor.can("storage.inspect")):
            if not store.owns_iceberg_table(table_name, actor.id):
                raise HTTPException(status_code=404, detail="Iceberg table not found")

    @app.post(
        "/api/datasets/iceberg",
        tags=["Iceberg"],
        summary="Iceberg Dataset 연결",
        status_code=201,
        response_model=DatasetOut,
    )
    def connect_table(body: IcebergConnect, actor: Actor = CREATE_DATASET_DEP):
        check_table_access(body.table_name, actor)
        return connect_iceberg(store, body.table_name, body.snapshot_id, owner_actor=actor.id)

    @app.get(
        "/api/iceberg/{table_name}/snapshots",
        tags=["Iceberg"],
        summary="Iceberg 테이블 Snapshot 조회",
        response_model=list[SnapshotOut],
    )
    def snapshots(table_name: str, actor: Actor = SNAPSHOT_READ_DEP):
        check_table_access(table_name, actor)
        return iceberg_snapshots(store, table_name)

    @app.post(
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
            exclude_ml_train=not discovery_only,
        )
        return page_from_rows(rows, limit)

    @app.get("/api/jobs", tags=["Jobs"], summary="작업 목록 조회", response_model=JobPageOut)
    def list_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return job_page(limit, cursor, actor, discovery_only=False)

    @app.get(
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

    app.include_router(
        training_router(
            store,
            existing_dataset=existing,
            existing_job=existing_job,
            require_submission_key=require_job_submission_key,
        )
    )

    @app.post(
        "/api/ml/scoring/jobs",
        tags=["Scoring"],
        summary="모델 점수화 작업 제출",
        description="확정된 모델과 Dataset을 사용해 별도의 배치 Scoring Job을 접수합니다. 학습과 독립적인 실행입니다.",
        status_code=202,
        response_model=MLScoringJobOut,
    )
    def create_scoring_job(
        body: MLScoringRequest,
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
        dataset = existing(body.dataset_id, actor)
        model = store.get_model_revision(body.model_revision_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model Revision not found")
        check_ownership(actor, model["owner_actor"], "model", model["id"])
        if not model.get("registry_version"):
            raise HTTPException(status_code=409, detail="Model Registry registration is pending")
        try:
            verify_registered_version(_management_client(), model)
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        require_job_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_score",
            dataset_id=dataset["id"],
            spec={
                "model_revision_id": model["id"],
                "model_owner_actor": model["owner_actor"],
                "dataset_version": dataset["version"],
                "label_column": body.label_column,
                "observation_id_column": body.observation_id_column,
                "temporal_scope": (
                    body.temporal_scope.model_dump(mode="json")
                    if body.temporal_scope is not None else None
                ),
            },
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
            idempotency_key=idempotency_key,
        )

    def existing_scoring_job(job_id: str, actor: Actor) -> dict:
        item = existing_job(job_id, actor)
        if item["kind"] != "ml_score":
            raise HTTPException(status_code=404, detail="Scoring Job not found")
        return item

    @app.get(
        "/api/ml/scoring/jobs",
        tags=["Scoring"],
        summary="점수화 작업 목록 조회",
        response_model=MLScoringJobPageOut,
    )
    def list_scoring_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        rows = store.jobs(
            limit=limit + 1,
            before=decode_cursor(cursor, "Job"),
            actor_id=None if actor.privileged else actor.id,
            kind="ml_score",
        )
        return page_from_rows(rows, limit)

    @app.get(
        "/api/ml/scoring/jobs/{job_id}",
        tags=["Scoring"],
        summary="점수화 작업 상세 조회",
        response_model=MLScoringJobOut,
    )
    def get_scoring_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return existing_scoring_job(job_id, actor)

    @app.post(
        "/api/ml/scoring/jobs/{job_id}/cancel",
        tags=["Scoring"],
        summary="점수화 작업 취소 요청",
        response_model=MLScoringJobOut,
    )
    def cancel_scoring_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        existing_scoring_job(job_id, actor)
        item = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if item is None:
            raise HTTPException(status_code=404, detail="Scoring Job not found")
        return item

    def required_scoring_result(result_id: str, actor: Actor) -> dict:
        scoring = store.get_scoring_result(result_id)
        if scoring is None:
            raise HTTPException(status_code=404, detail="Scoring Result not found")
        check_ownership(actor, scoring["owner_actor"], "scoring_result", result_id)
        return scoring

    @app.post(
        "/api/ml/policies/jobs",
        tags=["Decision Policies"],
        summary="판정 정책 작업 제출",
        description="Scoring 결과에 별도의 판정 정책을 생성합니다. 임계값을 바꾸기 위해 모델을 재학습할 필요는 없습니다.",
        status_code=202,
        response_model=MLPolicyJobOut,
    )
    def create_policy_job(
        body: MLPolicyRequest,
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
        score = required_scoring_result(body.scoring_result_id, actor)
        require_job_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_policy",
            dataset_id=score["dataset_id"],
            spec={
                **body.model_dump(),
                "source_owner_actor": score["owner_actor"],
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/api/ml/evaluations/jobs",
        tags=["Evaluations"],
        summary="평가 작업 제출",
        description="Scoring·정책·레이블의 실제 근거에 따라 평가를 실행합니다. 레이블 없는 결과에 감독 지표를 만들지 않습니다.",
        status_code=202,
        response_model=MLEvaluationJobOut,
    )
    def create_evaluation_job(
        body: MLEvaluationRequest,
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
        score = required_scoring_result(body.scoring_result_id, actor)
        if body.policy_revision_id:
            policy = store.get_decision_policy_revision(body.policy_revision_id)
            if policy is None:
                raise HTTPException(status_code=404, detail="Decision Policy not found")
            check_ownership(actor, policy["owner_actor"], "decision_policy", policy["id"])
            reference = required_scoring_result(policy["scoring_result_id"], actor)
            from .ml_decision import validate_policy_for_scoring

            try:
                validate_policy_for_scoring(store, policy, score)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        require_job_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_evaluate",
            dataset_id=score["dataset_id"],
            spec={
                **body.model_dump(),
                "source_owner_actor": score["owner_actor"],
                **({
                    "policy_owner_actor": policy["owner_actor"],
                    "reference_owner_actor": reference["owner_actor"],
                } if body.policy_revision_id else {}),
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    def ml_special_job(job_id: str, actor: Actor, kind: str) -> dict:
        item = existing_job(job_id, actor)
        if item["kind"] != kind:
            raise HTTPException(status_code=404, detail="ML Job not found")
        return item

    def list_ml_result_jobs(kind: str, limit: int, cursor: str | None, actor: Actor):
        rows = store.jobs(
            limit=limit + 1,
            before=decode_cursor(cursor, "Job"),
            actor_id=None if actor.privileged else actor.id,
            kind=kind,
        )
        return page_from_rows(rows, limit)

    @app.get(
        "/api/ml/policies/jobs",
        tags=["Decision Policies"],
        summary="판정 정책 작업 목록 조회",
        response_model=MLPolicyJobPageOut,
    )
    def list_ml_policy_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return list_ml_result_jobs("ml_policy", limit, cursor, actor)

    @app.get(
        "/api/ml/evaluations/jobs",
        tags=["Evaluations"],
        summary="평가 작업 목록 조회",
        response_model=MLEvaluationJobPageOut,
    )
    def list_ml_evaluation_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return list_ml_result_jobs("ml_evaluate", limit, cursor, actor)

    @app.get(
        "/api/ml/explanations/jobs",
        tags=["Explanations"],
        summary="설명 작업 목록 조회",
        response_model=MLExplanationJobPageOut,
    )
    def list_ml_explanation_jobs(
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        actor: Actor = READ_JOB_DEP,
    ):
        return list_ml_result_jobs("ml_explain", limit, cursor, actor)

    @app.get(
        "/api/ml/policies/jobs/{job_id}",
        tags=["Decision Policies"],
        summary="판정 정책 작업 상세 조회",
        response_model=MLPolicyJobOut,
    )
    def get_policy_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return ml_special_job(job_id, actor, "ml_policy")

    @app.post(
        "/api/ml/policies/jobs/{job_id}/cancel",
        tags=["Decision Policies"],
        summary="판정 정책 작업 취소 요청",
        response_model=MLPolicyJobOut,
    )
    def cancel_policy_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        ml_special_job(job_id, actor, "ml_policy")
        item = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if item is None:
            raise HTTPException(status_code=404, detail="ML Job not found")
        return item

    @app.get(
        "/api/ml/evaluations/jobs/{job_id}",
        tags=["Evaluations"],
        summary="평가 작업 상세 조회",
        response_model=MLEvaluationJobOut,
    )
    def get_evaluation_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return ml_special_job(job_id, actor, "ml_evaluate")

    @app.post(
        "/api/ml/evaluations/jobs/{job_id}/cancel",
        tags=["Evaluations"],
        summary="평가 작업 취소 요청",
        response_model=MLEvaluationJobOut,
    )
    def cancel_evaluation_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        ml_special_job(job_id, actor, "ml_evaluate")
        item = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if item is None:
            raise HTTPException(status_code=404, detail="ML Job not found")
        return item

    @app.get(
        "/api/ml/policies/{revision_id}",
        tags=["Decision Policies"],
        summary="판정 정책 판본 조회",
        response_model=DecisionPolicyRevisionOut,
    )
    def get_policy_revision(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_decision_policy_revision(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Decision Policy not found")
        check_ownership(actor, item["owner_actor"], "decision_policy", revision_id)
        return item

    @app.post(
        "/api/ml/explanations/jobs",
        tags=["Explanations"],
        summary="모델 설명 작업 제출",
        description="선택한 Scoring 결과의 설명을 계산합니다. Feature 민감도와 SHAP은 실제 이상 사건의 인과 원인이 아닙니다.",
        status_code=202,
        response_model=MLExplanationJobOut,
    )
    def create_explanation_job(
        body: MLExplanationRequest,
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
        score = required_scoring_result(body.scoring_result_id, actor)
        if body.row_index >= score["row_count"]:
            raise HTTPException(status_code=422, detail="Explanation row_index out of bounds")
        require_job_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_explain",
            dataset_id=score["dataset_id"],
            spec={
                "scoring_result_id": score["id"],
                "source_owner_actor": score["owner_actor"],
                "row_index": body.row_index,
                "method": body.method,
            },
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
            idempotency_key=idempotency_key,
        )

    @app.get(
        "/api/ml/explanations/jobs/{job_id}",
        tags=["Explanations"],
        summary="모델 설명 작업 상세 조회",
        response_model=MLExplanationJobOut,
    )
    def get_explanation_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return ml_special_job(job_id, actor, "ml_explain")

    @app.post(
        "/api/ml/explanations/jobs/{job_id}/cancel",
        tags=["Explanations"],
        summary="모델 설명 작업 취소 요청",
        response_model=MLExplanationJobOut,
    )
    def cancel_explanation_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        ml_special_job(job_id, actor, "ml_explain")
        item = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if item is None:
            raise HTTPException(status_code=404, detail="ML Job not found")
        return item

    @app.get(
        "/api/ml/explanations/{revision_id}",
        tags=["Explanations"],
        summary="모델 설명 결과 조회",
        response_model=ExplanationResultOut,
    )
    def get_explanation_result(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_explanation_result(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Explanation not found")
        check_ownership(actor, item["owner_actor"], "explanation", revision_id)
        return item

    @app.get(
        "/api/ml/evaluations/{revision_id}",
        tags=["Evaluations"],
        summary="평가 결과 판본 조회",
        response_model=EvaluationRevisionOut,
    )
    def get_evaluation_revision(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_evaluation_revision(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Evaluation not found")
        check_ownership(actor, item["owner_actor"], "evaluation", revision_id)
        return item

    @app.get(
        "/api/ml/scoring/results/{result_id}",
        tags=["Scoring"],
        summary="점수화 결과 조회",
        response_model=ScoringResultOut,
    )
    def get_scoring_result(result_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_scoring_result(result_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Scoring Result not found")
        check_ownership(actor, item["owner_actor"], "scoring_result", result_id)
        return item

    @app.get(
        "/api/ml/tracking/jobs/{job_id}",
        tags=["Experiments"],
        summary="MLflow 기록 및 등록 상태 조회",
        response_model=MLflowExportStatusOut,
    )
    def get_mlflow_tracking_status(job_id: str, actor: Actor = READ_JOB_DEP):
        item = existing_job(job_id, actor)
        if item["kind"] not in ("ml_train", "ml_score", "ml_policy", "ml_evaluate", "ml_explain"):
            raise HTTPException(status_code=404, detail="ML Job not found")
        status = store.tracking_status(job_id)
        return status or {"job_id": job_id, "state": "Disabled"}

    def _management_client() -> MLflowClient:
        try:
            settings = MLflowSettings.from_env()
        except RuntimeConfigurationError as exc:
            raise HTTPException(
                status_code=503, detail="MLflow management configuration is invalid"
            ) from exc
        if settings is None:
            raise HTTPException(status_code=503, detail="MLflow management is unavailable")
        return MLflowClient(settings)

    @app.get(
        "/api/ml/experiments",
        tags=["Experiments"],
        summary="MLflow 실험 목록 조회",
        response_model=MLflowExperimentPageOut,
    )
    def list_ml_experiments(
        page_token: str | None = Query(default=None, max_length=2048),
        actor: Actor = READ_JOB_DEP,
    ):
        try:
            return read_experiments(_management_client(), actor.id, page_token=page_token)
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(
        "/api/ml/experiments/runs",
        tags=["Experiments"],
        summary="MLflow 실행 기록 조회",
        response_model=MLflowRunPageOut,
    )
    def list_ml_runs(
        experiment_id: str | None = Query(default=None, pattern=r"^[0-9]+$"),
        page_token: str | None = Query(default=None, max_length=2048),
        actor: Actor = READ_JOB_DEP,
    ):
        try:
            return read_runs(
                _management_client(), store, actor.id,
                experiment_id=experiment_id, page_token=page_token,
            )
        except MLflowDeliveryError as exc:
            if str(exc) == "SOS-MLFLOW-EXPERIMENT-NOT-FOUND":
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(
        "/api/ml/registry/models",
        tags=["Models"],
        summary="MLflow 모델 버전 목록 조회",
        description="MLflow가 관리하는 모델 이름과 Registry 버전을 조회합니다. SOS의 불변 모델 실행 참조와 구분합니다.",
        response_model=MLflowModelVersionPageOut,
    )
    def list_registered_models(
        page_token: str | None = Query(default=None, max_length=2048),
        actor: Actor = READ_JOB_DEP,
    ):
        try:
            return read_model_versions(_management_client(), store, actor.id, page_token=page_token)
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(
        "/api/ml/models/{revision_id}",
        tags=["Models"],
        summary="모델 실행 판본 조회",
        description="Dataset·Job·Runtime·Feature·아티팩트 근거가 고정된 SOS 모델 실행 참조를 조회합니다.",
        response_model=ModelRevisionOut,
    )
    def get_model_revision(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_model_revision(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Model revision not found")
        check_ownership(actor, item["owner_actor"], "model", revision_id)
        return item

    @app.post(
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

    @app.post(
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

    @app.get(
        "/api/explorations/jobs/{job_id}",
        tags=["Data Exploration"],
        summary="데이터 탐색 작업 상세 조회",
        response_model=ExplorationJobOut,
    )
    def get_exploration_job(job_id: str, actor: Actor = READ_JOB_DEP):
        return exploration_job(job_id, actor)

    @app.post(
        "/api/explorations/jobs/{job_id}/cancel",
        tags=["Data Exploration"],
        summary="데이터 탐색 작업 취소 요청",
        response_model=ExplorationJobOut,
    )
    def cancel_exploration_job(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        exploration_job(job_id, actor)
        result = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @app.get("/api/jobs/{job_id}", tags=["Jobs"], summary="작업 상세 조회", response_model=JobOut)
    def get_job(job_id: str, actor: Actor = READ_JOB_DEP):
        item = existing_job(job_id, actor)
        if item["kind"] in (
            "exploration",
            "ml_train",
            "ml_score",
            "ml_policy",
            "ml_evaluate",
            "ml_explain",
        ):
            raise HTTPException(status_code=404, detail="Job not found")
        return item

    @app.post(
        "/api/jobs/{job_id}/cancel",
        tags=["Jobs"],
        summary="작업 취소 요청",
        description="취소를 요청하며 실제 작업 중단이나 외부 저장소 변경의 롤백을 보장하지 않습니다.",
        response_model=JobOut,
    )
    def cancel(job_id: str, actor: Actor = CANCEL_JOB_DEP):
        item = existing_job(job_id, actor)
        if item["kind"] in (
            "exploration",
            "ml_train",
            "ml_score",
            "ml_policy",
            "ml_evaluate",
            "ml_explain",
        ):
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = store.request_cancel(job_id, actor_id=actor.id, actor_type=actor.kind)
        except IcebergCancellationConflict as exc:
            raise PlatformError("SOS-ICEBERG-CANCEL-409") from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    # FastAPI 생성 명세를 그대로 사용하고 Scalar 탐색 그룹만 추가한다.
    original_openapi = app.openapi

    def grouped_openapi() -> dict:
        schema = original_openapi()
        schema["x-tagGroups"] = OPENAPI_TAG_GROUPS
        return schema

    app.openapi = grouped_openapi

    return app
