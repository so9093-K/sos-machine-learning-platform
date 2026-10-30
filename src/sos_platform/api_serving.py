"""온라인 서빙 Endpoint 관리와 동기 추론 API.

외부 소비자는 Ray가 아니라 이 API를 쓴다. SOS가 인증·모델 접근·입력 서명을 판정한 뒤
내부 Serve 경로로 중계한다. 배포·전환·중단(model.manage)과 추론 호출(model.infer)은
다른 권한이다. 실제 실행 상태는 Ray Serve에서 그때그때 읽고 저장하지 않는다.
"""

import math
import os
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StrictFloat, StrictInt

from .api_access import ResourceAccess
from .api_ml_models import management_client
from .errors import InvalidRequestError, PlatformError
from .ml_input import ordered_playground_input
from .mlflow_errors import MLflowDeliveryError
from .mlflow_registry import verify_registered_version
from .security import Actor, require
from .serving_client import (
    ServingClient,
    ServingRequestRejected,
    ServingUnavailable,
    application_name,
)
from .store import Store
from .store_serving import ServingChangeRejected

READ_JOB = Depends(require("job.read"))
MANAGE_MODEL = Depends(require("model.manage"))
INFER = Depends(require("model.infer"))
MAX_INPUTS = 100
# 확인에 성공한 Registry 상태를 재사용하는 시간. 보관·등록 무효화는 이 시간 안에 추론에 반영된다.
REGISTRY_CHECK_SECONDS = 30.0


class ServingEndpointCreate(BaseModel):
    name: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    model_revision_id: str = Field(min_length=1, max_length=36)


class ServingRevisionRequest(BaseModel):
    model_revision_id: str = Field(min_length=1, max_length=36)


class ServingStateRequest(BaseModel):
    desired_state: Literal["serving", "stopped"]


class ServingApplicationOut(BaseModel):
    model_revision_id: str
    status: str
    failure_reason: str | None = None


class ServingRuntimeOut(BaseModel):
    reachable: bool
    active: ServingApplicationOut | None = None
    pending: ServingApplicationOut | None = None


class ServingEndpointOut(BaseModel):
    id: str
    name: str
    desired_state: Literal["serving", "stopped"]
    ready: bool
    active_model_revision_id: str | None
    pending_model_revision_id: str | None = None
    previous_model_revision_id: str | None = None
    owned: bool = Field(description="호출한 계정이 이 Endpoint의 소유자다.")
    manageable: bool = Field(description="소유자 또는 관리자라 전환·중단·삭제를 할 수 있다.")
    owner_actor: str | None = Field(default=None, description="관리할 수 있는 사용자에게만 보인다.")
    created_at: str
    updated_at: str
    runtime: ServingRuntimeOut


class PredictionRequest(BaseModel):
    inputs: list[dict[str, StrictFloat | StrictInt | None]] = Field(
        min_length=1, max_length=MAX_INPUTS,
        description="관측값별 모델 입력 Feature. 이름은 모델 입력 서명과 같아야 하며 null은 결측이다.",
    )


class PredictionOut(BaseModel):
    endpoint_id: str
    model_revision_id: str
    output_type: str
    score_direction: str
    score_scale: str
    outputs: list[dict[str, float]]


def _failure_reason(application: dict) -> str | None:
    """traceback 대신 마지막 원인 문장만 소유자에게 보인다."""
    messages = [application.get("message") or ""] + [
        deployment.get("message") or "" for deployment in (application.get("deployments") or {}).values()]
    lines = [line.strip() for message in messages for line in message.splitlines() if line.strip()]
    if not lines:
        return None
    # "sos_platform.errors.PlatformError: 사유"처럼 예외 경로가 붙은 끝 줄에서 사유만 남긴다.
    head, separator, reason = lines[-1].partition(": ")
    return (reason if separator and "." in head and " " not in head else lines[-1])[:300]


def serving_router(store: Store, access: ResourceAccess) -> APIRouter:
    router = APIRouter()
    client = ServingClient.from_env()
    timeout = float(os.getenv("SOS_SERVING_TIMEOUT_SECONDS", "10"))

    def runtime_applications() -> dict[str, dict] | None:
        if client is None:
            return None
        try:
            return client.applications()
        except (ServingUnavailable, ServingRequestRejected):
            return None

    # 보관·등록 무효화는 Playground·Scoring과 같은 Registry 검증으로 판정한다. 확인에 성공한
    # 판본만 잠시 재사용하며, MLflow 장애로 확인할 수 없으면 다른 새 사용과 같이 거절한다.
    registry_checked: dict[str, float] = {}

    def require_registered(model: dict) -> None:
        checked_at = registry_checked.get(model["id"])
        if checked_at is not None and time.monotonic() - checked_at < REGISTRY_CHECK_SECONDS:
            return
        try:
            verify_registered_version(management_client(), model)
        except MLflowDeliveryError as exc:
            registry_checked.pop(model["id"], None)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        registry_checked[model["id"]] = time.monotonic()

    def endpoint_view(endpoint: dict, actor: Actor, manageable: bool,
                      applications: dict[str, dict] | None) -> dict:
        def application(revision_id: str | None) -> dict | None:
            if not revision_id or applications is None:
                return None
            app = applications.get(application_name(endpoint["id"], revision_id))
            status = app.get("status", "UNKNOWN") if app else "NOT_DEPLOYED"
            failed = app is not None and status in ("DEPLOY_FAILED", "UNHEALTHY")
            return {"model_revision_id": revision_id, "status": status,
                    "failure_reason": _failure_reason(app) if manageable and failed else None}
        active = application(endpoint["active_model_revision_id"])
        return {
            **{key: endpoint[key] for key in ("id", "name", "desired_state", "created_at",
                                             "updated_at", "active_model_revision_id")},
            # 전환·롤백 대상과 소유자는 운영 정보다. 소비자에게는 활성 판본만 보인다.
            "pending_model_revision_id": endpoint["pending_model_revision_id"] if manageable else None,
            "previous_model_revision_id": endpoint["previous_model_revision_id"] if manageable else None,
            "owned": endpoint["owner_actor"] == actor.id,
            "manageable": manageable,
            "owner_actor": endpoint["owner_actor"] if manageable else None,
            "ready": endpoint["desired_state"] == "serving" and bool(active)
                and active["status"] == "RUNNING",
            "runtime": {"reachable": applications is not None, "active": active,
                        "pending": application(endpoint["pending_model_revision_id"])
                        if manageable else None},
        }

    def visible_endpoint(endpoint_id: str, actor: Actor) -> tuple[dict, bool]:
        """소유자·관리자가 아니면 활성 판본이 지금 제공 중일 때만 보인다."""
        endpoint = store.get_serving_endpoint(endpoint_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="Endpoint를 찾을 수 없습니다.")
        if actor.privileged or endpoint["owner_actor"] == actor.id:
            return endpoint, True
        active = endpoint["active_model_revision_id"]
        offering = store.get_model_offering(active) if active else None
        if endpoint["desired_state"] == "serving" and offering and offering["withdrawn_at"] is None:
            return endpoint, False
        access.check_ownership(actor, endpoint["owner_actor"], "serving_endpoint", endpoint_id)
        return endpoint, True

    def managed_endpoint(endpoint_id: str, actor: Actor) -> dict:
        """전환·중단·삭제 대상. 소유자 또는 관리자만 다룬다."""
        endpoint = store.get_serving_endpoint(endpoint_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="Endpoint를 찾을 수 없습니다.")
        access.check_ownership(actor, endpoint["owner_actor"], "serving_endpoint", endpoint_id)
        return endpoint

    def change(action):
        try:
            return action()
        except ServingChangeRejected as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get(
        "/api/serving/endpoints",
        tags=["Serving"],
        summary="온라인 서빙 Endpoint 조회",
        description="소유자·관리자는 자신의(관리자는 전체) Endpoint를, 그 밖의 사용자는 지정한 모델을 "
        "지금 제공 중인 Endpoint만 봅니다. 실제 준비 상태는 서빙 Runtime에서 조회합니다.",
        response_model=list[ServingEndpointOut],
    )
    def list_endpoints(
        model_revision_id: str | None = Query(default=None, max_length=36),
        actor: Actor = READ_JOB,
    ):
        managed = store.list_serving_endpoints(
            owner_actor=None if actor.privileged else actor.id, model_revision_id=model_revision_id)
        shared = [] if actor.privileged or model_revision_id is None else [
            item for item in store.list_offered_serving_endpoints(model_revision_id)
            if item["owner_actor"] != actor.id]
        applications = runtime_applications()
        return [endpoint_view(item, actor, True, applications) for item in managed] + [
            endpoint_view(item, actor, False, applications) for item in shared]

    @router.post(
        "/api/serving/endpoints",
        tags=["Serving"],
        summary="온라인 서빙 Endpoint 생성",
        description="소유한 활성 모델 판본으로 Endpoint를 만듭니다. 판본은 서빙 Runtime에서 준비가 "
        "확인된 뒤 활성 판본이 됩니다. 모델 제공과 별개입니다.",
        status_code=201,
        response_model=ServingEndpointOut,
    )
    def create_endpoint(body: ServingEndpointCreate, request: Request,
                        actor: Actor = MANAGE_MODEL):
        require_registered(access.owned_model(body.model_revision_id, actor))
        endpoint = change(lambda: store.create_serving_endpoint(
            name=body.name, model_revision_id=body.model_revision_id, owner_actor=actor.id,
            owner_actor_type=actor.kind, privileged=actor.privileged,
            request_id=request.state.request_id))
        return endpoint_view(endpoint, actor, True, runtime_applications())

    @router.get(
        "/api/serving/endpoints/{endpoint_id}",
        tags=["Serving"],
        summary="온라인 서빙 Endpoint 상태 조회",
        response_model=ServingEndpointOut,
    )
    def get_endpoint(endpoint_id: str, actor: Actor = READ_JOB):
        endpoint, manageable = visible_endpoint(endpoint_id, actor)
        return endpoint_view(endpoint, actor, manageable, runtime_applications())

    @router.put(
        "/api/serving/endpoints/{endpoint_id}/revision",
        tags=["Serving"],
        summary="서빙 판본 전환 요청",
        description="새 판본을 준비하고, 서빙 Runtime이 실행 중으로 확인한 뒤 활성 판본을 바꿉니다. "
        "준비되기 전이나 실패해도 현재 판본이 계속 요청을 처리합니다.",
        response_model=ServingEndpointOut,
    )
    def request_revision(endpoint_id: str, body: ServingRevisionRequest, request: Request,
                         actor: Actor = MANAGE_MODEL):
        managed_endpoint(endpoint_id, actor)
        require_registered(access.model(body.model_revision_id))
        endpoint = change(lambda: store.request_serving_revision(
            endpoint_id, body.model_revision_id, actor_id=actor.id, actor_type=actor.kind,
            privileged=actor.privileged, request_id=request.state.request_id))
        return endpoint_view(endpoint, actor, True, runtime_applications())

    @router.post(
        "/api/serving/endpoints/{endpoint_id}/rollback",
        tags=["Serving"],
        summary="직전 판본으로 되돌리기",
        description="직전 판본을 다시 준비해 전환합니다. 준비 전까지 현재 판본이 계속 요청을 처리합니다.",
        response_model=ServingEndpointOut,
    )
    def rollback(endpoint_id: str, request: Request, actor: Actor = MANAGE_MODEL):
        previous = managed_endpoint(endpoint_id, actor)["previous_model_revision_id"]
        if previous:
            require_registered(access.model(previous))
        endpoint = change(lambda: store.rollback_serving_endpoint(
            endpoint_id, actor_id=actor.id, actor_type=actor.kind, privileged=actor.privileged,
            request_id=request.state.request_id))
        return endpoint_view(endpoint, actor, True, runtime_applications())

    @router.delete(
        "/api/serving/endpoints/{endpoint_id}/revision-request",
        tags=["Serving"],
        summary="판본 전환 요청 취소",
        response_model=ServingEndpointOut,
    )
    def cancel_revision(endpoint_id: str, request: Request, actor: Actor = MANAGE_MODEL):
        managed_endpoint(endpoint_id, actor)
        endpoint = change(lambda: store.cancel_serving_revision(
            endpoint_id, actor_id=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id))
        return endpoint_view(endpoint, actor, True, runtime_applications())

    @router.put(
        "/api/serving/endpoints/{endpoint_id}/state",
        tags=["Serving"],
        summary="서빙 중단·재개",
        description="중단하면 서빙 Runtime에서 내려가 새 요청을 받지 않습니다. 기록과 판본은 유지됩니다.",
        response_model=ServingEndpointOut,
    )
    def set_state(endpoint_id: str, body: ServingStateRequest, request: Request,
                  actor: Actor = MANAGE_MODEL):
        managed_endpoint(endpoint_id, actor)
        endpoint = change(lambda: store.set_serving_state(
            endpoint_id, body.desired_state, actor_id=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id))
        return endpoint_view(endpoint, actor, True, runtime_applications())

    @router.delete(
        "/api/serving/endpoints/{endpoint_id}",
        tags=["Serving"],
        summary="중단된 Endpoint 삭제",
        status_code=204,
    )
    def delete_endpoint(endpoint_id: str, request: Request, actor: Actor = MANAGE_MODEL):
        managed_endpoint(endpoint_id, actor)
        change(lambda: store.delete_serving_endpoint(
            endpoint_id, actor_id=actor.id, actor_type=actor.kind,
            request_id=request.state.request_id))

    @router.post(
        "/api/serving/endpoints/{endpoint_id}/predict",
        tags=["Serving"],
        summary="온라인 추론",
        description="Endpoint의 활성 판본으로 관측값을 바로 계산합니다. 요청은 Job으로 남지 않습니다. "
        "다른 사용자의 Endpoint는 활성 판본이 제공 중일 때만 호출할 수 있습니다. 보관됐거나 Registry 상태를 "
        "확인할 수 없는 판본은 Playground·Scoring과 같이 새 계산에 쓰지 않습니다.",
        response_model=PredictionOut,
    )
    def predict(endpoint_id: str, body: PredictionRequest, actor: Actor = INFER):
        endpoint, _ = visible_endpoint(endpoint_id, actor)
        revision_id = endpoint["active_model_revision_id"]
        if endpoint["desired_state"] != "serving" or not revision_id:
            raise PlatformError("SOS-SERVING-NOT-READY")
        model = access.model_allowed(actor, access.model(revision_id), for_new_work=True)
        require_registered(model)
        rows = []
        for index, values in enumerate(body.inputs, start=1):
            try:
                ordered = ordered_playground_input(model, values)
            except ValueError as exc:
                raise InvalidRequestError(
                    f"{index}번째 관측값이 모델의 입력 Feature 이름·자료형·결측 조건과 맞지 않습니다."
                ) from exc
            # JSON은 NaN을 담지 못하므로 결측은 null로 전달하고 서빙 Runtime이 되돌린다.
            rows.append([None if math.isnan(value) else value for value in ordered])
        if client is None:
            raise PlatformError("SOS-SERVING-UNAVAILABLE")
        try:
            result = client.predict(endpoint_id, revision_id, rows, timeout)
        except ServingRequestRejected as exc:
            # 404는 Ray가 재시작돼 애플리케이션이 아직 다시 준비되지 않은 상태다.
            raise PlatformError("SOS-SERVING-NOT-READY" if exc.status == 404
                                else "SOS-SERVING-UNAVAILABLE") from exc
        except ServingUnavailable as exc:
            raise PlatformError("SOS-SERVING-UNAVAILABLE") from exc
        outputs = result.get("outputs")
        if result.get("model_revision_id") != revision_id or not isinstance(outputs, list) \
                or len(outputs) != len(rows):
            raise PlatformError("SOS-SERVING-UNAVAILABLE")
        return {"endpoint_id": endpoint_id, "model_revision_id": revision_id,
                "output_type": result["output_type"], "score_direction": result["score_direction"],
                "score_scale": result["score_scale"], "outputs": outputs}

    return router
