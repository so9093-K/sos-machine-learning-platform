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

    def instance_display_name(self) -> str:
        """표시 전용 인스턴스명. 제품 브랜드·실행 환경·권한과 독립적이다."""
        with self.connection() as db:
            return (
                db.execute(
                    text("SELECT value FROM instance_settings WHERE key='display_name'")
                ).scalar_one_or_none()
                or "Local Workspace"
            )

    def set_instance_display_name(
        self, value: str, *, actor_id: str, actor_type: str, request_id: str | None
    ) -> None:
        """이름 변경과 감사 기록을 같은 트랜잭션에 기록한다."""
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO instance_settings(key,value,updated_at,updated_by) "
                    "VALUES('display_name',:value,:at,:actor) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                    "updated_at=excluded.updated_at,updated_by=excluded.updated_by"
                ),
                {"value": value, "at": now(), "actor": actor_id},
            )
            self._audit(
                db,
                actor_id=actor_id,
                actor_type=actor_type,
                action="instance.configure",
                resource_type="instance",
                resource_id="display_name",
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

