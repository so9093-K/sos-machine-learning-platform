"""SOS 모델 그룹과 판본 보관(ADR 0025).

그룹의 이름·설명·보관과 판본 보관은 SOS가 정본이며 MLflow에 반영하지 않는다. 보관은 새 사용만
막고 아티팩트·기존 결과를 지우지 않는다. 제공 중이거나 서빙 중인 판본은 먼저 제공·서빙을 중단해야
보관할 수 있다. 그룹 삭제만 MLflow에 남은 빈 Registered Model을 함께 정리한다.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, text

from .errors import PlatformError, RuntimeConfigurationError
from .mlflow_config import MLflowSettings
from .mlflow_errors import MLflowDeliveryError
from .mlflow_registry import registered_model_name
from .mlflow_tracking import MLflowClient
from .model_use import locked_model
from .schemas import (
    ModelArchiveOut,
    ModelGroupCreate,
    ModelGroupListOut,
    ModelGroupOut,
    ModelGroupUpdate,
)
from .security import Actor, require
from .store import Store
from .store_administration import now

READ = Depends(require("job.read"))
MANAGE = Depends(require("model.manage"))

# 판본이 다른 Actor에게 제공 중이거나 서빙 의도로 쓰이고 있는지. 보관은 이런 판본을 갑자기 끊지 않는다.
_IN_USE = (
    "(EXISTS (SELECT 1 FROM model_offerings o WHERE o.model_revision_id=m.id "
    "AND o.withdrawn_at IS NULL) OR EXISTS (SELECT 1 FROM serving_endpoints e "
    "WHERE e.desired_state='serving' AND m.id IN (e.active_model_revision_id, "
    "e.pending_model_revision_id)))"
)
_IN_USE_DETAIL = "제공 중이거나 서빙 중인 판본이 있습니다. 먼저 제공·서빙을 중단하세요."


def _group(db: Connection, owner: str, key: str, *, lock: bool = False) -> dict:
    row = db.execute(text(
        "SELECT * FROM model_groups WHERE owner_actor=:owner AND registry_key=:key"
        + (" FOR UPDATE" if lock else "")
    ), {"owner": owner, "key": key}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="모델 그룹을 찾을 수 없습니다.")
    return dict(row)


def _mlflow() -> MLflowClient | None:
    """MLflow 연결을 구성하지 않았으면 None. 구성이 잘못됐으면 외부 정리를 시작하지 않는다."""
    try:
        settings = MLflowSettings.from_env()
    except RuntimeConfigurationError as exc:
        raise HTTPException(status_code=503, detail="MLflow 연결 설정을 확인하세요.") from exc
    return MLflowClient(settings) if settings is not None else None


def model_groups_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ml/model-groups", tags=["Models"], summary="모델 그룹 목록",
                response_model=ModelGroupListOut)
    def list_groups(actor: Actor = READ):
        with store.connection() as db:
            rows = db.execute(text(
                "SELECT * FROM model_groups WHERE owner_actor=:owner ORDER BY registry_key"
            ), {"owner": actor.id}).mappings().all()
        return {"items": [dict(row) for row in rows]}

    @router.post("/api/ml/model-groups", tags=["Models"], summary="모델 그룹 생성",
                 status_code=201, response_model=ModelGroupOut)
    def create_group(body: ModelGroupCreate, actor: Actor = MANAGE):
        if body.registry_key == "default":
            raise HTTPException(status_code=409, detail="기본 모델 그룹 키는 예약되어 있습니다.")
        at = now()
        with store.connection() as db:
            created = db.execute(text(
                "INSERT INTO model_groups(owner_actor,registry_key,title,description,archived_at,"
                "created_at,updated_at) VALUES(:owner,:key,:title,:description,NULL,:at,:at) "
                "ON CONFLICT DO NOTHING RETURNING *"
            ), {"owner": actor.id, "key": body.registry_key, "title": body.title.strip(),
                "description": body.description, "at": at}).mappings().first()
            if created is None:
                raise HTTPException(status_code=409, detail="이미 존재하는 모델 그룹입니다.")
            store._audit(db, actor_id=actor.id, actor_type=actor.kind, action="model_group.create",
                         resource_type="model_group", resource_id=body.registry_key)
        return dict(created)

    @router.patch("/api/ml/model-groups/{registry_key}", tags=["Models"],
                  summary="모델 그룹 이름·설명 변경과 보관·복원",
                  description="보관한 그룹의 판본은 새 Scoring·Playground·추론·서빙·제공에 쓰지 않고, "
                  "그룹에 새 학습·연결 판본을 만들지 않습니다. 제공·서빙 중인 판본이 있으면 보관할 수 "
                  "없습니다.",
                  response_model=ModelGroupOut)
    def update_group(registry_key: str, body: ModelGroupUpdate, actor: Actor = MANAGE):
        with store.connection() as db:
            group = _group(db, actor.id, registry_key, lock=True)
            if body.archived and db.execute(text(
                f"SELECT 1 FROM model_revisions m WHERE m.owner_actor=:owner "
                f"AND m.registry_key=:key AND m.lifecycle_state='Active' AND {_IN_USE} LIMIT 1"
            ), {"owner": actor.id, "key": registry_key}).first():
                raise HTTPException(status_code=409, detail=_IN_USE_DETAIL)
            archived_at = group["archived_at"]
            if body.archived is not None:
                archived_at = (archived_at or now()) if body.archived else None
            updated = db.execute(text(
                "UPDATE model_groups SET title=:title,description=:description,"
                "archived_at=:archived_at,updated_at=:at "
                "WHERE owner_actor=:owner AND registry_key=:key RETURNING *"
            ), {"title": body.title.strip() if body.title is not None else group["title"],
                "description": body.description if body.description is not None
                else group["description"],
                "archived_at": archived_at, "at": now(), "owner": actor.id,
                "key": registry_key}).mappings().one()
            action = ("model_group.archive" if body.archived else "model_group.restore"
                      if body.archived is not None else "model_group.update")
            store._audit(db, actor_id=actor.id, actor_type=actor.kind, action=action,
                         resource_type="model_group", resource_id=registry_key)
        return dict(updated)

    @router.delete("/api/ml/model-groups/{registry_key}", tags=["Models"], summary="모델 그룹 삭제",
                   description="삭제되지 않은 판본이나 진행 중인 학습·연결이 없는 그룹만 삭제합니다. "
                   "MLflow에 남은 빈 Registered Model도 함께 정리합니다.")
    def delete_group(registry_key: str, actor: Actor = MANAGE):
        if registry_key == "default":
            raise HTTPException(status_code=409, detail="기본 모델 그룹은 삭제할 수 없습니다.")

        def ensure_empty(db: Connection) -> None:
            _group(db, actor.id, registry_key, lock=True)
            if db.execute(text(
                "SELECT 1 FROM model_revisions WHERE owner_actor=:owner AND registry_key=:key "
                "AND lifecycle_state!='Deleted' LIMIT 1"
            ), {"owner": actor.id, "key": registry_key}).first():
                raise HTTPException(status_code=409, detail="모델 그룹에 관리 중인 모델 판본이 남아 있습니다.")
            if db.execute(text(
                "SELECT 1 FROM jobs WHERE kind IN ('ml_train','ml_import') AND owner_actor=:owner "
                "AND status IN ('Queued','Running') "
                "AND COALESCE(spec::jsonb->>'registry_key','default')=:key LIMIT 1"
            ), {"owner": actor.id, "key": registry_key}).first():
                raise HTTPException(status_code=409, detail="모델 그룹에 실행 중인 학습·연결 작업이 있습니다.")

        with store.connection() as db:
            ensure_empty(db)
        client = _mlflow()
        if client is not None:
            name = registered_model_name({
                "owner_actor": actor.id, "algorithm": "isolation_forest", "registry_key": registry_key,
            })
            try:
                client.delete("registered-models/delete", {"name": name})
            except MLflowDeliveryError as exc:
                if not str(exc).startswith("SOS-MLFLOW-HTTP-404"):
                    raise HTTPException(status_code=503, detail="MLflow에 남은 모델 그룹을 정리하지 "
                                        "못했습니다. 상태를 확인한 뒤 다시 요청하세요.") from exc
        with store.connection() as db:
            ensure_empty(db)
            db.execute(text(
                "DELETE FROM model_groups WHERE owner_actor=:owner AND registry_key=:key"
            ), {"owner": actor.id, "key": registry_key})
            store._audit(db, actor_id=actor.id, actor_type=actor.kind, action="model_group.delete",
                         resource_type="model_group", resource_id=registry_key)
        return {"registry_key": registry_key, "deleted": True}

    def set_archived(revision_id: str, archived: bool, actor: Actor) -> dict:
        with store.connection() as db:
            model = locked_model(db, revision_id, for_update=True)
            if model is None or (not actor.privileged and model["owner_actor"] != actor.id):
                raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")
            if model["lifecycle_state"] != "Active":
                raise PlatformError("SOS-ML-MODEL-INVALID")
            if archived and db.execute(text(
                f"SELECT 1 FROM model_revisions m WHERE m.id=:id AND {_IN_USE}"
            ), {"id": revision_id}).first():
                raise HTTPException(status_code=409, detail=_IN_USE_DETAIL)
            archived_at = (model["archived_at"] or now()) if archived else None
            db.execute(text("UPDATE model_revisions SET archived_at=:at WHERE id=:id"),
                       {"at": archived_at, "id": revision_id})
            store._audit(db, actor_id=actor.id, actor_type=actor.kind,
                         action="model.archive" if archived else "model.restore",
                         resource_type="model", resource_id=revision_id)
        return {"model_revision_id": revision_id, "archived_at": archived_at}

    @router.put("/api/ml/models/{revision_id}/archive", tags=["Models"], summary="모델 판본 보관",
                description="새 Scoring·Playground·추론·서빙·제공에서 제외합니다. 아티팩트와 기존 결과는 "
                "유지합니다. 제공·서빙 중인 판본은 먼저 중단하세요.",
                response_model=ModelArchiveOut)
    def archive_model(revision_id: str, actor: Actor = MANAGE):
        return set_archived(revision_id, True, actor)

    @router.delete("/api/ml/models/{revision_id}/archive", tags=["Models"], summary="모델 판본 복원",
                   response_model=ModelArchiveOut)
    def restore_model(revision_id: str, actor: Actor = MANAGE):
        return set_archived(revision_id, False, actor)

    return router
