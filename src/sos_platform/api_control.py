"""헬스·인증된 인스턴스·감사·Job 진단의 API 책임.

공통 FastAPI 애플리케이션의 보안/오류 미들웨어는 그대로 사용한다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import IdentitySettings
from .object_store import ObjectStore
from .schemas import (
    AuditEventOut,
    AuthMeOut,
    InstanceSettingsOut,
    InstanceSettingsUpdate,
    JobDiagnosticsOut,
)
from .security import ACTIONS, Actor, current_actor, require
from .store import Store

IDENTITY_DEP = Depends(current_actor)
AUDIT_DEP = Depends(require("audit.read"))
INSTANCE_CONFIG_DEP = Depends(require("instance.configure"))


def control_router(store: Store, identity: IdentitySettings) -> APIRouter:
    """제어 API의 기존 URL·권한·응답 모델을 변경하지 않고 별도 Router로 등록한다."""
    router = APIRouter()

    @router.get("/api/health")
    def health():
        """프로세스 자체가 HTTP 응답을 제공하는지 확인한다."""
        return {"status": "ok"}

    @router.get("/api/ready")
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

    @router.get("/api/v2/auth/me", response_model=AuthMeOut)
    def auth_me(actor: Actor = IDENTITY_DEP):
        return {
            "actor_id": actor.id,
            "actor_type": actor.kind,
            "roles": sorted(actor.roles),
            "capabilities": sorted(action for action in ACTIONS if actor.can(action)),
            "mode": identity.mode,
        }

    @router.get("/api/instance", response_model=InstanceSettingsOut)
    def instance_settings(actor: Actor = IDENTITY_DEP):
        """인증된 클라이언트가 동일한 인스턴스 정보를 받는다."""
        return {
            "display_name": store.instance_display_name(),
        }

    @router.put("/api/instance", response_model=InstanceSettingsOut)
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

    @router.get("/api/v2/audit", response_model=list[AuditEventOut])
    def audit_events(
        limit: int = Query(default=50, ge=1, le=100),
        request_id: str | None = Query(default=None, min_length=36, max_length=36),
        job_id: str | None = Query(default=None, min_length=36, max_length=36),
        actor: Actor = AUDIT_DEP,
    ):
        """운영자만 요청 ID 또는 Job ID의 과거 변경·거부 이력을 조회한다."""
        return store.audit_events(limit=limit, request_id=request_id, resource_id=job_id)

    @router.get("/api/v2/diagnostics/jobs/{job_id}", response_model=JobDiagnosticsOut)
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

    return router
