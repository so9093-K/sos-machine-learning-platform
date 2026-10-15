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

ACTIONS = {
    "dataset.read",
    "dataset.create",
    "job.read",
    "job.create",
    "job.cancel",
    "storage.inspect",
    "snapshot.retain",
    "audit.read",
}
GRANTS = {
    "viewer": {"dataset.read", "job.read"},
    "researcher": {"dataset.read", "dataset.create", "job.read", "job.create", "job.cancel"},
    "operator": {"storage.inspect", "snapshot.retain", "audit.read"},
    "administrator": ACTIONS,
}


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
        mode = os.getenv("SOS_AUTH_MODE", "development")
        if mode not in ("development", "token"):
            raise ValueError("SOS_AUTH_MODE must be development or token")
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
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization[7:]
        if len(token) < 32 or len(token) > 4096:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        for expected, actor in self.token_subjects.items():
            if secrets.compare_digest(fingerprint, expected):
                return actor
        raise HTTPException(
            status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"}
        )


def current_actor(request: Request) -> Actor:
    if request.app.state.identity.mode == "development":
        peer = request.client.host if request.client else ""
        if peer not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(status_code=403, detail="Development identity is local only")
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
            raise HTTPException(status_code=403, detail="Permission denied")
        return actor

    return dependency


def resource_allowed(actor: Actor, owner: str | None) -> None:
    """소유권이 없는 기존 데이터는 관리자를 제외하고 기본 거부한다."""
    if not actor.privileged and owner != actor.id:
        raise HTTPException(status_code=404, detail="Resource not found")
