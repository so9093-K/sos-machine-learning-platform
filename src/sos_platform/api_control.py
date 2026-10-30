"""헬스·인증된 인스턴스·감사·Job 진단의 API 책임.

공통 FastAPI 애플리케이션의 보안/오류 미들웨어는 그대로 사용한다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .object_store import ObjectStore
from .schemas import (
    AuditEventOut,
    BrandingSettingsOut,
    BrandingSettingsUpdate,
    AuthMeOut,
    InstanceSettingsOut,
    InstanceSettingsUpdate,
    JobDiagnosticsOut,
    HealthOut,
    ReadyOut,
)
from .security import ACTIONS, Actor, IdentitySettings, current_actor, require
from .serving_client import ServingClient
from .store import Store

IDENTITY_DEP = Depends(current_actor)
AUDIT_DEP = Depends(require("audit.read"))
INSTANCE_CONFIG_DEP = Depends(require("instance.configure"))


def control_router(store: Store, identity: IdentitySettings) -> APIRouter:
    """제어 API의 기존 URL·권한·응답 모델을 변경하지 않고 별도 Router로 등록한다."""
    router = APIRouter()
    serving = ServingClient.from_env()

    @router.get(
        "/api/health",
        tags=["System"],
        summary="API 프로세스 상태 확인",
        description="프로세스가 HTTP에 응답하는지만 확인합니다. 저장소와 Worker의 준비 상태는 Ready API로 확인하세요.",
        response_model=HealthOut,
    )
    def health():
        """프로세스 자체가 HTTP 응답을 제공하는지 확인한다."""
        return {"status": "ok"}

    @router.get(
        "/api/ready",
        tags=["System"],
        summary="준비 상태 확인",
        description="API에 필요한 데이터베이스·객체 저장소가 준비되지 않으면 503을 반환합니다. "
        "Job 실행(Worker)과 온라인 서빙 Runtime은 API와 따로 실행되는 기능이므로 features에 상태를 "
        "보고하고, 하나라도 멈추면 status가 degraded(200)입니다.",
        response_model=ReadyOut,
        responses={503: {"model": ReadyOut, "description": "API에 필요한 저장소가 준비되지 않음"}},
    )
    def ready():
        """API 전체 장애와 일부 기능 장애를 구별한다. Worker가 멈춰도 온라인 추론은 계속 될 수 있다."""
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
        features = {}
        try:
            features["job_execution"] = "ready" if store.worker_ready() else "unavailable"
        except Exception:
            features["job_execution"] = "unavailable"
        features["online_serving"] = ("not_configured" if serving is None
                                      else "ready" if serving.reachable() else "unavailable")
        status = ("unavailable" if any(v != "ready" for v in checks.values())
                  else "degraded" if "unavailable" in features.values() else "ready")
        result = {"status": status, "services": checks, "features": features}
        if status == "unavailable":
            return JSONResponse(status_code=503, content=result)
        return result

    @router.get(
        "/api/v2/auth/me",
        tags=["Identity"],
        summary="현재 신원과 권한 조회",
        description="현재 Actor의 역할과 허용된 기능을 조회합니다. Token 모드에서는 Bearer 인증이 필요합니다.",
        response_model=AuthMeOut,
    )
    def auth_me(actor: Actor = IDENTITY_DEP):
        return {
            "actor_id": actor.id,
            "actor_type": actor.kind,
            "roles": sorted(actor.roles),
            "capabilities": sorted(action for action in ACTIONS if actor.can(action)),
            "mode": identity.mode,
        }

    @router.get(
        "/api/branding",
        tags=["Identity"],
        summary="공개 제품 브랜드 조회",
        description="로그인 화면에서도 사용할 수 있는 표시 브랜드명만 반환합니다.",
        response_model=BrandingSettingsOut,
    )
    def branding_settings(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"brand_name": store.brand_name()}

    @router.put(
        "/api/branding",
        tags=["Identity"],
        summary="제품 브랜드명 변경",
        response_model=BrandingSettingsOut,
    )
    def change_branding_settings(
        body: BrandingSettingsUpdate,
        request: Request,
        actor: Actor = INSTANCE_CONFIG_DEP,
    ):
        store.set_brand_name(
            body.brand_name,
            actor_id=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
        )
        return {"brand_name": body.brand_name}

    @router.get(
        "/api/instance",
        tags=["Identity"],
        summary="인스턴스 설정 조회",
        response_model=InstanceSettingsOut,
    )
    def instance_settings(actor: Actor = IDENTITY_DEP):
        """인증된 클라이언트가 동일한 인스턴스 정보를 받는다."""
        return {
            "display_name": store.instance_display_name(),
        }

    @router.put(
        "/api/instance",
        tags=["Identity"],
        summary="인스턴스 표시명 변경",
        response_model=InstanceSettingsOut,
    )
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

    @router.get(
        "/api/v2/audit",
        tags=["Audit & Diagnostics"],
        summary="접근 및 변경 감사 기록 조회",
        response_model=list[AuditEventOut],
    )
    def audit_events(
        limit: int = Query(default=50, ge=1, le=100),
        request_id: str | None = Query(default=None, min_length=36, max_length=36),
        job_id: str | None = Query(default=None, min_length=36, max_length=36),
        actor: Actor = AUDIT_DEP,
    ):
        """운영자만 요청 ID 또는 Job ID의 과거 변경·거부 이력을 조회한다."""
        return store.audit_events(limit=limit, request_id=request_id, resource_id=job_id)

    @router.get(
        "/api/v2/diagnostics/jobs/{job_id}",
        tags=["Audit & Diagnostics"],
        summary="작업 실패 진단 정보 조회",
        response_model=JobDiagnosticsOut,
    )
    def job_diagnostics(job_id: str, actor: Actor = AUDIT_DEP):
        """권한을 확인한 운영자에게만 정제된 원인 유형·실행 단계를 제공한다."""
        item = store.get_job(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
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

    return router
