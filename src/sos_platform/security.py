"""인증 제공자와 데이터·기능 권한의 분리.

로컬 개발용 신원은 개발 모드에서만 사용한다. 토큰 모드의 토큰은
고엔트로피 불투명 Bearer 값이며 환경에는 SHA-256 지문만 보관한다.
외부 인증 공급자 어댑터를 도입해도 Actor·Action·Resource 계약은 유지된다.
"""

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ACTIONS = {
    "dataset.read",
    "dataset.create",
    "snapshot.read",
    "job.read",
    "job.create",
    "job.cancel",
    "model.manage",
    "model.train",
    "feature.manage",
    "storage.inspect",
    "snapshot.retain",
    "audit.read",
    "instance.configure",
}
GRANTS = {
    "viewer": {"dataset.read", "job.read", "snapshot.read"},
    "researcher": {
        "dataset.read",
        "dataset.create",
        "snapshot.read",
        "job.read",
        "job.create",
        "job.cancel",
    },
    "operator": {"storage.inspect", "snapshot.read", "snapshot.retain", "audit.read"},
    "administrator": ACTIONS,
}

# 모델·Feature는 개발 환경에서 만들고 배포 자동화가 공급한다. 학습·정의 변경·모델 제공은
# 사람 사용자 역할이 아니라 서비스 계정의 자동화 역할과 관리자에게만 허용한다.
GRANTS["automation"] = GRANTS["researcher"] | {"model.train", "model.manage", "feature.manage"}
SERVICE_ONLY_ROLES = frozenset({"automation"})


@dataclass(frozen=True)
class Actor:
    id: str
    kind: str
    roles: frozenset[str]

    def can(self, action: str) -> bool:
        return action in {permission for role in self.roles for permission in GRANTS[role]}

    @property
    def privileged(self) -> bool:
        return "administrator" in self.roles


@dataclass(frozen=True)
class IdentitySettings:
    mode: str
    token_subjects: dict[str, Actor]

    @classmethod
    def from_env(cls) -> "IdentitySettings":
        mode = os.getenv("SOS_AUTH_MODE")
        if mode not in ("development", "token"):
            raise ValueError("SOS_AUTH_MODE must be explicitly set to development or token")
        if mode == "development":
            if os.getenv("SOS_AUTH_TOKEN_HASHES"):
                raise ValueError("Token map is not supported in development mode")
            return cls(mode, {})
        try:
            values = json.loads(os.environ["SOS_AUTH_TOKEN_HASHES"])
            if not isinstance(values, dict) or not values:
                raise ValueError("A nonempty token map is required")
            mapping = {}
            for fingerprint, item in values.items():
                if not re.fullmatch("[0-9a-f]{64}", fingerprint):
                    raise ValueError("Tokens must be stored as SHA-256 fingerprints")
                actor_id, kind, roles = item["actor_id"], item["kind"], item["roles"]
                if (
                    not isinstance(actor_id, str)
                    or not re.fullmatch(r"[a-zA-Z0-9_.:@/-]{1,128}", actor_id)
                    or kind not in ("human", "service")
                    or not isinstance(roles, list)
                    or not roles
                    or not all(isinstance(role, str) and role in GRANTS for role in roles)
                    or (kind != "service" and SERVICE_ONLY_ROLES.intersection(roles))
                ):
                    raise ValueError("Invalid actor or role map")
                mapping[fingerprint] = Actor(actor_id, kind, frozenset(roles))
            return cls(mode, mapping)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Invalid SOS_AUTH_TOKEN_HASHES actor configuration") from exc

    def authenticate(self, authorization: str | None) -> Actor:
        if self.mode == "development":
            return Actor("local-developer", "human", frozenset({"administrator"}))
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="인증이 필요합니다.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization[7:]
        if len(token) < 32 or len(token) > 4096:
            raise HTTPException(
                status_code=401,
                detail="인증 정보를 확인하세요.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        for expected, actor in self.token_subjects.items():
            if secrets.compare_digest(fingerprint, expected):
                return actor
        raise HTTPException(
            status_code=401, detail="인증 정보를 확인하세요.", headers={"WWW-Authenticate": "Bearer"}
        )


# 인증 판정은 기존 IdentitySettings가 담당한다. 표준 보안 의존성은 OpenAPI에도 Bearer를
# 선언하기 위한 것이며, auto_error=False로 기존 401/403 동작을 변경하지 않는다.
OPENAPI_BEARER = HTTPBearer(
    auto_error=False,
    description="Token 모드에서는 Bearer 토큰이 필요합니다. 로컬 development 모드는 토큰 없이 동작합니다.",
)


def current_actor(
    request: Request,
    _bearer: HTTPAuthorizationCredentials | None = Depends(OPENAPI_BEARER),
) -> Actor:
    if request.app.state.identity.mode == "development":
        peer = request.client.host if request.client else ""
        # 프록시가 원격 사용자를 로컬 클라이언트처럼 전달하는 실수를 차단한다.
        # 전달 헤더를 제거하는 프록시는 감지할 수 없으므로 외부 공개 자체가 금지된다.
        proxy_headers = ("forwarded", "x-forwarded-for", "x-real-ip", "x-forwarded-host", "via")
        if peer not in ("127.0.0.1", "::1", "testclient") or any(
            header in request.headers for header in proxy_headers
        ):
            raise HTTPException(status_code=403, detail="개발 신원은 로컬에서만 사용할 수 있습니다.")
    actor = request.app.state.identity.authenticate(request.headers.get("authorization"))
    request.state.actor = actor
    return actor


CURRENT_ACTOR_DEP = Depends(current_actor)


def require(action: str):
    """모든 진입점에서 동일한 Action 권한을 검사한다."""
    if action not in ACTIONS:
        raise ValueError("Unknown permission")

    def dependency(actor: Actor = CURRENT_ACTOR_DEP) -> Actor:
        if not actor.can(action):
            raise HTTPException(status_code=403, detail="요청한 작업에 접근할 수 없습니다.")
        return actor

    return dependency


def resource_allowed(actor: Actor, owner: str | None) -> None:
    """소유권이 없는 기존 데이터는 관리자를 제외하고 기본 거부한다."""
    if not actor.privileged and owner != actor.id:
        raise HTTPException(status_code=404, detail="요청한 리소스를 찾을 수 없습니다.")
