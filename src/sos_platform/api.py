"""데이터 탐색과 비동기 처리를 제공하는 공통 플랫폼 API."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from scalar_fastapi import AgentScalarConfig, get_scalar_api_reference
from starlette.concurrency import run_in_threadpool
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from .api_access import ResourceAccess
from .api_control import control_router
from .api_datasets import dataset_router
from .api_errors import problem_response, register_error_handlers
from .api_features import feast_router
from .api_jobs import job_router
from .api_ml_models import ml_model_router
from .api_ml_results import ml_results_router
from .api_ml_training import training_router
from .api_model_management import model_management_router
from .api_model_offering import model_offering_router
from .api_pipelines import pipeline_router
from .api_storage import storage_router
from .dagster_client import DagsterUnavailable
from .errors import ERRORS
from .ingestion import MULTIPART_OVERHEAD_BYTES, UploadPolicy
from .model_deletion import model_deletion_router
from .observability import emit
from .schemas import ProblemOut
from .scoring_observations import scoring_observations_router
from .security import IdentitySettings
from .store import Store

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

공개 계약은 [OpenAPI JSON](/openapi.json)입니다. 일반적인 API 오류는 분류·코드·요청 ID를 포함한 구조화된 응답을 사용합니다. `/api/ready`의 503은 준비되지 않은 서비스를 나타내는 상태 응답입니다.
"""

OPENAPI_TAGS = [
    {"name": "System", "description": "프로세스 생존 여부 및 필수 서비스의 준비 상태"},
    {"name": "Identity", "description": "인증 신원, 권한 및 인스턴스 표시 설정"},
    {"name": "Datasets", "description": "데이터 파일·합성 예제·Dataset 판본 등록과 조회"},
    {"name": "Data Exploration", "description": "Dataset 구조·프로파일·데이터 조회 및 탐색 실행"},
    {"name": "Data Preparation", "description": "Feature 변환과 분석용 입력 데이터 준비"},
    {"name": "Features", "description": "Feast 등록 Feature와 Point-in-Time Historical Retrieval"},
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
    {"name": "Platform", "tags": ["System", "Identity"]},
    {
        "name": "Data",
        "tags": ["Datasets", "Data Exploration", "Data Preparation", "Features", "Iceberg"],
    },
    {
        "name": "Research & ML",
        "tags": [
            "Experiments", "Training", "Models", "Scoring",
            "Decision Policies", "Evaluations", "Explanations",
        ],
    },
    {"name": "Pipelines", "tags": ["Pipeline Catalog", "Pipeline Runs"]},
    {"name": "Operations", "tags": ["Jobs", "Storage", "Audit & Diagnostics"]},
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

    @app.exception_handler(DagsterUnavailable)
    async def dagster_unavailable(request: Request, exc: DagsterUnavailable):
        # 실제 실패 종류만 기록한다. GraphQL 응답·사용자 데이터·자격 증명은 남기지 않는다.
        emit(
            "dagster.request.failed",
            service="sos-api",
            level=logging.ERROR,
            request_id=request.state.request_id,
            error_code="SOS-API-DEPENDENCY",
            operation=exc.operation,
            failure_type=exc.failure_type,
            status=str(exc.http_status) if exc.http_status is not None else None,
        )
        return problem_response(request, "SOS-API-DEPENDENCY", 503,
                                ERRORS["SOS-API-DEPENDENCY"].message)

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
        # 로그인 화면의 자격 증명 없는 신원 확인은 정상적인 초기 조회다.
        # 잘못된 인증 헤더와 다른 API의 접근 거부는 계속 감사한다.
        anonymous_identity_probe = (
            request.method == "GET"
            and request.url.path == "/api/v2/auth/me"
            and "authorization" not in request.headers
            and response.status_code == 401
        )
        if response.status_code in (401, 403) and not anonymous_identity_probe:
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

    # 라우터는 접근 판정을 복제하지 않고 같은 ResourceAccess를 사용한다.
    access = ResourceAccess(store, identity.mode)

    @app.get("/docs/assets/scalar.js", include_in_schema=False)
    def scalar_asset():
        """빌드에 포함된 Scalar 브라우저 자산을 같은 출처에서 제공한다."""
        path = Path(__file__).resolve().parent / "static" / "scalar.js"
        if not path.is_file():
            raise HTTPException(
                status_code=503,
                detail="Scalar 문서 자산이 없습니다. scripts/build_scalar_assets.sh를 실행하세요.",
            )
        return FileResponse(path, media_type="application/javascript")

    @app.get("/docs", include_in_schema=False)
    def scalar_docs():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
            scalar_js_url="/docs/assets/scalar.js",
            scalar_favicon_url=(
                "data:image/svg+xml,"
                "%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2064%2064'%3E"
                "%3Crect%20width='64'%20height='64'%20rx='12'%20fill='%23172554'/%3E"
                "%3Ctext%20x='32'%20y='43'%20text-anchor='middle'%20font-size='28'"
                "%20font-family='sans-serif'%20fill='white'%3ES%3C/text%3E%3C/svg%3E"
            ),
            with_default_fonts=False,
            agent=AgentScalarConfig(disabled=True),
            telemetry=False,
        )

    app.include_router(control_router(store, identity))
    app.include_router(pipeline_router(store, access, identity_mode=identity.mode))
    app.include_router(scoring_observations_router(store, access))
    app.include_router(storage_router(store))
    app.include_router(dataset_router(store, access, upload_policy))
    app.include_router(job_router(store, access))
    app.include_router(model_management_router(store))
    app.include_router(model_offering_router(store, access))
    app.include_router(model_deletion_router(store))
    app.include_router(feast_router(store, access))
    app.include_router(training_router(store, access))
    app.include_router(ml_results_router(store, access))
    app.include_router(ml_model_router(store, access))

    # FastAPI 생성 명세를 그대로 사용하고 Scalar 탐색 그룹만 추가한다.
    original_openapi = app.openapi

    def grouped_openapi() -> dict:
        schema = original_openapi()
        schema["x-tagGroups"] = OPENAPI_TAG_GROUPS
        return schema

    app.openapi = grouped_openapi

    return app
