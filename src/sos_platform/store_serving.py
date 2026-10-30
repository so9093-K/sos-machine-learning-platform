"""온라인 서빙 Endpoint의 배포 의도 저장.

실제 실행 상태는 Ray Serve가 정본이다. 여기서는 어떤 판본을 서빙해야 하는지와 전환·롤백
대상만 다룬다. 의도 변경은 Endpoint 행을 잠근 뒤 모델 행을 공유 잠금으로 확인해, 같은 모델
행을 잠그는 영구 삭제와 순서를 정한다.
"""

from uuid import uuid4

from sqlalchemy import text

from .store_administration import now
from .store_models import _model_item

CONTROLLER_ACTOR = "sos-serving-controller"


class ServingChangeRejected(Exception):
    """현재 상태에서 받을 수 없는 배포 의도 변경. 사유 문장은 사용자 안내에 그대로 쓴다."""


# 서빙 의도의 복제본 수 판정을 직렬화한다. 동시에 접수된 변경이 함께 수용량을 넘지 않게 한다.
_CAPACITY_LOCK = "SELECT pg_advisory_xact_lock(hashtext('sos.serving.capacity'))"


class ServingStoreMixin:
    def _servable_model(self, db, revision_id: str, endpoint_owner: str, privileged: bool) -> dict:
        row = db.execute(text(
            "SELECT id,owner_actor,lifecycle_state,registry_version FROM model_revisions "
            "WHERE id=:id FOR SHARE"
        ), {"id": revision_id}).mappings().first()
        if row is None or (not privileged and row["owner_actor"] != endpoint_owner):
            raise ServingChangeRejected("서빙할 모델 판본을 찾을 수 없습니다.")
        if row["lifecycle_state"] != "Active" or not row["registry_version"]:
            raise ServingChangeRejected("Registry 등록이 확인된 활성 모델만 서빙할 수 있습니다.")
        return dict(row)

    def _admit_replicas(self, db, endpoint_id: str, revisions: set[str], capacity: int) -> None:
        """이 Endpoint가 변경 뒤 서빙할 판본까지 더해도 서빙 Runtime이 실행할 수 있는지 확인한다.

        새 판본은 기존 판본 옆에서 준비되므로 전환 대상도 복제본 하나를 차지한다.
        """
        db.execute(text(_CAPACITY_LOCK))
        others = db.execute(text(
            "SELECT count(*) FROM serving_endpoints e JOIN model_revisions m ON m.id IN "
            "(e.active_model_revision_id, e.pending_model_revision_id) "
            "WHERE e.desired_state='serving' AND m.lifecycle_state='Active' AND e.id<>:id"
        ), {"id": endpoint_id}).scalar_one()
        needed = others + len(revisions)
        if needed > capacity:
            raise ServingChangeRejected(
                f"서빙 Runtime 자원이 부족합니다. 이 변경 뒤 복제본 {needed}개가 필요하지만 지금 "
                f"{capacity}개까지 실행할 수 있습니다. 새 판본은 기존 판본 옆에서 준비되므로 전환 중에는 "
                "복제본이 하나 더 필요합니다. 쓰지 않는 Endpoint를 중단하거나 서빙 CPU(SOS_SERVING_CPUS)를 "
                "늘리세요.")

    def _locked_endpoint(self, db, endpoint_id: str) -> dict:
        row = db.execute(text("SELECT * FROM serving_endpoints WHERE id=:id FOR UPDATE"),
                         {"id": endpoint_id}).mappings().first()
        if row is None:
            raise ServingChangeRejected("Endpoint를 찾을 수 없습니다.")
        return dict(row)

    def get_serving_endpoint(self, endpoint_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(text("SELECT * FROM serving_endpoints WHERE id=:id"),
                             {"id": endpoint_id}).mappings().first()
        return None if row is None else dict(row)

    def list_serving_endpoints(self, *, owner_actor: str | None,
                               model_revision_id: str | None = None) -> list[dict]:
        """소유자(관리자는 None으로 전체)의 Endpoint. 모델을 지정하면 그 판본을 쓰는 것만 돌려준다."""
        filters, params = [], {}
        if owner_actor is not None:
            filters.append("owner_actor=:owner")
            params["owner"] = owner_actor
        if model_revision_id is not None:
            filters.append("(active_model_revision_id=:model OR pending_model_revision_id=:model)")
            params["model"] = model_revision_id
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.connection() as db:
            rows = db.execute(text(
                f"SELECT * FROM serving_endpoints{where} ORDER BY created_at DESC, id DESC"
            ), params).mappings().all()
        return [dict(row) for row in rows]

    def list_offered_serving_endpoints(self, model_revision_id: str) -> list[dict]:
        """다른 사용자가 호출할 수 있는 Endpoint: 활성 판본이 지금 제공 중이고 서빙 의도가 유지된다."""
        with self.connection() as db:
            rows = db.execute(text(
                "SELECT e.* FROM serving_endpoints e JOIN model_offerings o "
                "ON o.model_revision_id=e.active_model_revision_id "
                "WHERE e.active_model_revision_id=:model AND e.desired_state='serving' "
                "AND o.withdrawn_at IS NULL ORDER BY e.created_at DESC, e.id DESC"
            ), {"model": model_revision_id}).mappings().all()
        return [dict(row) for row in rows]

    def create_serving_endpoint(self, *, name: str, model_revision_id: str, owner_actor: str,
                                owner_actor_type: str, privileged: bool, replica_capacity: int,
                                request_id: str | None = None) -> dict:
        """첫 판본은 전환 대상으로 두고, 준비가 확인되면 컨트롤러가 활성 판본으로 승격한다."""
        endpoint_id = str(uuid4())
        at = now()
        with self.connection() as db:
            self._servable_model(db, model_revision_id, owner_actor, privileged)
            self._admit_replicas(db, endpoint_id, {model_revision_id}, replica_capacity)
            inserted = db.execute(text(
                "INSERT INTO serving_endpoints(id,name,owner_actor,owner_actor_type,desired_state,"
                "pending_model_revision_id,created_at,updated_at) "
                "VALUES(:id,:name,:owner,:type,'serving',:model,:at,:at) "
                "ON CONFLICT(owner_actor,name) DO NOTHING RETURNING id"
            ), {"id": endpoint_id, "name": name, "owner": owner_actor, "type": owner_actor_type,
                "model": model_revision_id, "at": at}).scalar_one_or_none()
            if inserted is None:
                raise ServingChangeRejected("같은 이름의 Endpoint가 이미 있습니다.")
            self._audit(db, actor_id=owner_actor, actor_type=owner_actor_type,
                        action="serving.endpoint_created", resource_type="serving_endpoint",
                        resource_id=endpoint_id, request_id=request_id)
        return self.get_serving_endpoint(endpoint_id)

    def _request_revision(self, db, endpoint: dict, model_revision_id: str, *, actor_id: str,
                          actor_type: str, privileged: bool, replica_capacity: int, action: str,
                          request_id: str | None) -> None:
        self._servable_model(db, model_revision_id, endpoint["owner_actor"], privileged)
        pending = None if model_revision_id == endpoint["active_model_revision_id"] \
            else model_revision_id
        if endpoint["desired_state"] == "serving":
            self._admit_replicas(db, endpoint["id"], {
                revision for revision in (endpoint["active_model_revision_id"], pending) if revision
            }, replica_capacity)
        db.execute(text(
            "UPDATE serving_endpoints SET pending_model_revision_id=:pending,updated_at=:at "
            "WHERE id=:id"
        ), {"pending": pending, "at": now(), "id": endpoint["id"]})
        self._audit(db, actor_id=actor_id, actor_type=actor_type, action=action,
                    resource_type="serving_endpoint", resource_id=endpoint["id"],
                    reason=model_revision_id, request_id=request_id)

    def request_serving_revision(self, endpoint_id: str, model_revision_id: str, *,
                                 actor_id: str, actor_type: str, privileged: bool,
                                 replica_capacity: int, request_id: str | None = None) -> dict:
        """새 판본을 전환 대상으로 둔다. 기존 활성 판본은 새 판본이 준비될 때까지 계속 서빙한다."""
        with self.connection() as db:
            endpoint = self._locked_endpoint(db, endpoint_id)
            self._request_revision(db, endpoint, model_revision_id, actor_id=actor_id,
                                   actor_type=actor_type, privileged=privileged,
                                   replica_capacity=replica_capacity,
                                   action="serving.revision_requested", request_id=request_id)
        return self.get_serving_endpoint(endpoint_id)

    def rollback_serving_endpoint(self, endpoint_id: str, *, actor_id: str, actor_type: str,
                                  privileged: bool, replica_capacity: int,
                                  request_id: str | None = None) -> dict:
        """직전 판본을 다시 준비해 전환한다. 준비 전까지 현재 판본이 계속 서빙한다."""
        with self.connection() as db:
            endpoint = self._locked_endpoint(db, endpoint_id)
            if not endpoint["previous_model_revision_id"]:
                raise ServingChangeRejected("되돌릴 직전 판본이 없습니다.")
            self._request_revision(db, endpoint, endpoint["previous_model_revision_id"],
                                   actor_id=actor_id, actor_type=actor_type, privileged=privileged,
                                   replica_capacity=replica_capacity,
                                   action="serving.rollback_requested", request_id=request_id)
        return self.get_serving_endpoint(endpoint_id)

    def cancel_serving_revision(self, endpoint_id: str, *, actor_id: str, actor_type: str,
                                request_id: str | None = None) -> dict:
        with self.connection() as db:
            endpoint = self._locked_endpoint(db, endpoint_id)
            if endpoint["pending_model_revision_id"] and not endpoint["active_model_revision_id"]:
                raise ServingChangeRejected(
                    "첫 배포는 취소할 수 없습니다. Endpoint를 중단한 뒤 삭제하세요.")
            db.execute(text(
                "UPDATE serving_endpoints SET pending_model_revision_id=NULL,updated_at=:at "
                "WHERE id=:id"
            ), {"at": now(), "id": endpoint_id})
            self._audit(db, actor_id=actor_id, actor_type=actor_type,
                        action="serving.revision_cancelled", resource_type="serving_endpoint",
                        resource_id=endpoint_id, request_id=request_id)
        return self.get_serving_endpoint(endpoint_id)

    def set_serving_state(self, endpoint_id: str, desired_state: str, *, actor_id: str,
                          actor_type: str, replica_capacity: int | None = None,
                          request_id: str | None = None) -> dict:
        """재개는 서빙 Runtime이 그 판본들을 실행할 수 있을 때만 받는다. 중단은 언제나 받는다."""
        if desired_state not in ("serving", "stopped"):
            raise ServingChangeRejected("알 수 없는 서빙 상태입니다.")
        with self.connection() as db:
            endpoint = self._locked_endpoint(db, endpoint_id)
            if desired_state == "serving" and endpoint["desired_state"] != "serving":
                if replica_capacity is None:
                    raise ServingChangeRejected("서빙 Runtime의 수용량을 확인하지 못했습니다.")
                self._admit_replicas(db, endpoint_id, {
                    revision for revision in (endpoint["active_model_revision_id"],
                                              endpoint["pending_model_revision_id"]) if revision
                }, replica_capacity)
            db.execute(text(
                "UPDATE serving_endpoints SET desired_state=:state,updated_at=:at WHERE id=:id"
            ), {"state": desired_state, "at": now(), "id": endpoint_id})
            self._audit(db, actor_id=actor_id, actor_type=actor_type,
                        action="serving.endpoint_resumed" if desired_state == "serving"
                        else "serving.endpoint_stopped",
                        resource_type="serving_endpoint", resource_id=endpoint_id,
                        request_id=request_id)
        return self.get_serving_endpoint(endpoint_id)

    def delete_serving_endpoint(self, endpoint_id: str, *, actor_id: str, actor_type: str,
                                request_id: str | None = None) -> None:
        """중단된 Endpoint만 지운다. 서빙 중인 Endpoint가 사라져 소비자가 갑자기 끊기지 않게 한다."""
        with self.connection() as db:
            endpoint = self._locked_endpoint(db, endpoint_id)
            if endpoint["desired_state"] != "stopped":
                raise ServingChangeRejected("서빙 중인 Endpoint는 먼저 중단해야 삭제할 수 있습니다.")
            db.execute(text("DELETE FROM serving_endpoints WHERE id=:id"), {"id": endpoint_id})
            self._audit(db, actor_id=actor_id, actor_type=actor_type,
                        action="serving.endpoint_deleted", resource_type="serving_endpoint",
                        resource_id=endpoint_id, request_id=request_id)

    def serving_applications(self) -> list[dict]:
        """Ray Serve에 있어야 할 애플리케이션: 서빙 의도의 활성·전환 대상 판본과 그 불변 기록."""
        with self.connection() as db:
            rows = db.execute(text(
                "SELECT e.id AS endpoint_id, m.* FROM serving_endpoints e "
                "JOIN model_revisions m ON m.id IN "
                "(e.active_model_revision_id, e.pending_model_revision_id) "
                "WHERE e.desired_state='serving' AND m.lifecycle_state='Active' "
                "ORDER BY e.id, m.id"
            )).mappings().all()
        applications = []
        for row in rows:
            item = dict(row)
            endpoint_id = item.pop("endpoint_id")
            applications.append({"endpoint_id": endpoint_id, "revision": _model_item(item)})
        return applications

    def promote_serving_revision(self, endpoint_id: str, revision_id: str) -> bool:
        """준비가 확인된 전환 대상을 활성 판본으로 바꾼다. 그사이 의도가 바뀌었으면 아무것도 하지 않는다."""
        with self.connection() as db:
            promoted = db.execute(text(
                "UPDATE serving_endpoints SET "
                "previous_model_revision_id=COALESCE(active_model_revision_id,"
                "previous_model_revision_id),"
                "active_model_revision_id=pending_model_revision_id,"
                "pending_model_revision_id=NULL,updated_at=:at "
                "WHERE id=:id AND pending_model_revision_id=:revision AND desired_state='serving' "
                "RETURNING id"
            ), {"id": endpoint_id, "revision": revision_id, "at": now()}).scalar_one_or_none()
            if promoted:
                self._audit(db, actor_id=CONTROLLER_ACTOR, actor_type="service",
                            action="serving.revision_activated", resource_type="serving_endpoint",
                            resource_id=endpoint_id, reason=revision_id)
        return promoted is not None
