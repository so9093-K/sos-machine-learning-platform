"""감사 기록과 인스턴스 설정의 저장 계약.

Store의 트랜잭션 컨텍스트를 사용하며 다른 도메인의 실행·저장 정책은 정의하지 않는다.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text


def now() -> str:
    return datetime.now(UTC).isoformat()


class AdministrationStoreMixin:
    """감사 이벤트와 운영자 설정을 하나의 PostgreSQL 트랜잭션에서 관리한다."""

    @staticmethod
    def _audit(
        db,
        *,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        decision: str = "allow",
        outcome: str = "succeeded",
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """감사 기록이 실패하면 동일 PostgreSQL 트랜잭션의 변경도 실패한다."""
        db.execute(
            text(
                "INSERT INTO audit_events"
                "(id,at,actor_id,actor_type,action,resource_type,resource_id,"
                "decision,outcome,reason,request_id) "
                "VALUES(:id,:at,:actor_id,:actor_type,:action,:resource_type,"
                ":resource_id,:decision,:outcome,:reason,:request_id)"
            ),
            {
                "id": str(uuid4()),
                "at": now(),
                "actor_id": actor_id,
                "actor_type": actor_type,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "decision": decision,
                "outcome": outcome,
                "reason": reason,
                "request_id": request_id,
            },
        )

    def record_audit(
        self,
        *,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        decision: str = "deny",
        outcome: str = "denied",
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        with self.connection() as db:
            self._audit(
                db,
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                decision=decision,
                outcome=outcome,
                reason=reason,
                request_id=request_id,
            )

    def _instance_setting(self, key: str, default: str) -> str:
        """설치 인스턴스의 표시 설정을 한 저장소에서 읽는다."""
        with self.connection() as db:
            return (
                db.execute(
                    text("SELECT value FROM instance_settings WHERE key=:key"),
                    {"key": key},
                ).scalar_one_or_none()
                or default
            )

    def instance_display_name(self) -> str:
        """설치 인스턴스의 표시명. 제품 브랜드와 별개다."""
        return self._instance_setting("display_name", "Local Workspace")

    def brand_name(self) -> str:
        """사용자에게 표시하는 제품 브랜드명. 내부 식별자와 별개다."""
        return self._instance_setting("brand_name", "SOS")

    def _set_instance_setting(
        self, key: str, value: str, *, actor_id: str, actor_type: str,
        request_id: str | None,
    ) -> None:
        """표시 설정 변경과 감사 이벤트를 같은 트랜잭션에서 확정한다."""
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO instance_settings(key,value,updated_at,updated_by) "
                    "VALUES(:key,:value,:at,:actor) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                    "updated_at=excluded.updated_at,updated_by=excluded.updated_by"
                ),
                {"key": key, "value": value, "at": now(), "actor": actor_id},
            )
            self._audit(
                db,
                actor_id=actor_id,
                actor_type=actor_type,
                action="instance.configure",
                resource_type="instance",
                resource_id=key,
                request_id=request_id,
            )

    def set_instance_display_name(
        self, value: str, *, actor_id: str, actor_type: str, request_id: str | None
    ) -> None:
        self._set_instance_setting(
            "display_name", value, actor_id=actor_id, actor_type=actor_type,
            request_id=request_id,
        )

    def set_brand_name(
        self, value: str, *, actor_id: str, actor_type: str, request_id: str | None
    ) -> None:
        self._set_instance_setting(
            "brand_name", value, actor_id=actor_id, actor_type=actor_type,
            request_id=request_id,
        )

    def audit_events(
        self,
        *,
        limit: int = 50,
        request_id: str | None = None,
        resource_id: str | None = None,
    ) -> list[dict]:
        """운영자만 조회하는 감사 이력을 요청·작업 식별자로 제한한다."""
        if not 1 <= limit <= 100:
            raise ValueError("Audit limit out of range")
        clauses = []
        params = {"limit": limit}
        if request_id is not None:
            clauses.append("request_id=:request_id")
            params["request_id"] = request_id
        if resource_id is not None:
            clauses.append("resource_id=:resource_id")
            params["resource_id"] = resource_id
        predicate = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(
                        f"SELECT * FROM audit_events {predicate} "
                        "ORDER BY at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                ).mappings()
            ]
