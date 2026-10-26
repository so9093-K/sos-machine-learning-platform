"""MLflow Registered Model 관리. SOS는 소유 Actor를 검증하고 이중 Registry를 만들지 않는다."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from pydantic import BaseModel, Field

from .mlflow_errors import MLflowDeliveryError
from .mlflow_registry import registered_model_name
from .mlflow_tracking import MLflowClient
from .mlflow_config import MLflowSettings
from .security import Actor, require
from .store import Store

READ = Depends(require("job.read"))
MANAGE = Depends(require("model.manage"))
KEY_PATTERN = r"^[a-z][a-z0-9-]{0,39}$"


class NewModelGroup(BaseModel):
    registry_key: str = Field(pattern=KEY_PATTERN)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)


class ModelGroupUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    archived: bool | None = None


class VersionArchiveUpdate(BaseModel):
    archived: bool


def _client() -> MLflowClient:
    return MLflowClient(MLflowSettings.from_env())


def _name(actor_id: str, key: str) -> str:
    if not re.fullmatch(KEY_PATTERN, key):
        raise HTTPException(status_code=422, detail="Invalid model group key")
    return registered_model_name({
        "owner_actor": actor_id, "algorithm": "isolation_forest", "registry_key": key,
    })


def _tags(record: dict) -> dict[str, str]:
    return {
        tag["key"]: tag["value"] for tag in record.get("tags") or []
        if isinstance(tag, dict) and isinstance(tag.get("key"), str)
        and isinstance(tag.get("value"), str)
    }


def _get_group(client: MLflowClient, name: str) -> dict:
    try:
        record = client.get("registered-models/get", {"name": name}).get("registered_model")
    except MLflowDeliveryError as exc:
        if str(exc).startswith("SOS-MLFLOW-HTTP-404"):
            raise HTTPException(status_code=404, detail="Model group not found") from exc
        raise
    if not isinstance(record, dict) or record.get("name") != name:
        raise HTTPException(status_code=503, detail="Invalid Registry model response")
    return record


def _view(actor_id: str, record: dict) -> dict:
    name = record["name"]
    base = _name(actor_id, "default")
    if name == base:
        key = "default"
    elif name.startswith(base + "-"):
        key = name[len(base)+1:]
        if not re.fullmatch(KEY_PATTERN, key):
            raise HTTPException(status_code=503, detail="Invalid model group key")
    else:
        raise HTTPException(status_code=404, detail="Model group not found")
    tags = _tags(record)
    return {
        "registry_key": key, "name": name,
        "title": tags.get("sos.title") or ("기본 활동 이상 탐지" if key == "default" else key),
        "description": record.get("description") or "",
        "archived": tags.get("sos.archived") == "true",
    }


def model_management_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ml/registry/groups", tags=["Models"])
    def groups(
        page_token: str | None = Query(default=None, max_length=2048),
        actor: Actor = READ,
    ):
        client = _client()
        params = {"max_results": 100}
        if page_token:
            params["page_token"] = page_token
        try:
            result = client.get("registered-models/search", params)
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        records = result.get("registered_models") or []
        if not isinstance(records, list):
            raise HTTPException(status_code=503, detail="Invalid Registry model response")
        prefix = _name(actor.id, "default")
        visible = [
            _view(actor.id, record) for record in records
            if isinstance(record, dict)
            and (record.get("name") == prefix or str(record.get("name", "")).startswith(prefix + "-"))
        ]
        return {"items": visible, "next_page_token": result.get("next_page_token")}

    @router.post("/api/ml/registry/groups", tags=["Models"], status_code=201)
    def create_group(body: NewModelGroup, actor: Actor = MANAGE):
        if body.registry_key == "default":
            raise HTTPException(status_code=409, detail="Default model group is reserved")
        client = _client()
        name = _name(actor.id, body.registry_key)
        try:
            _get_group(client, name)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            raise HTTPException(status_code=409, detail="Model group already exists")
        try:
            result = client.post("registered-models/create", {
                "name": name,
                "description": body.description,
                "tags": [
                    {"key": "sos.source", "value": "sos-platform"},
                    {"key": "sos.title", "value": body.title.strip()},
                    {"key": "sos.archived", "value": "false"},
                ],
            })
            item = result.get("registered_model")
            if not isinstance(item, dict) or item.get("name") != name:
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            return _view(actor.id, item)
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.patch("/api/ml/registry/groups/{registry_key}", tags=["Models"])
    def update_group(registry_key: str, body: ModelGroupUpdate, actor: Actor = MANAGE):
        name = _name(actor.id, registry_key)
        client = _client()
        try:
            _get_group(client, name)
            if body.description is not None:
                client.patch("registered-models/update", {
                    "name": name, "description": body.description,
                })
            if body.title is not None:
                client.post("registered-models/set-tag", {
                    "name": name, "key": "sos.title", "value": body.title.strip(),
                })
            if body.archived is not None:
                client.post("registered-models/set-tag", {
                    "name": name, "key": "sos.archived",
                    "value": "true" if body.archived else "false",
                })
            return _view(actor.id, _get_group(client, name))
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.patch("/api/ml/registry/groups/{registry_key}/versions/{version}/archive",
                  tags=["Models"])
    def archive_version(registry_key: str, version: str, body: VersionArchiveUpdate,
                        actor: Actor = MANAGE):
        if not re.fullmatch(r"[1-9][0-9]*", version):
            raise HTTPException(status_code=422, detail="Invalid version")
        name = _name(actor.id, registry_key)
        client = _client()
        try:
            _get_group(client, name)
            record = client.get("model-versions/get", {
                "name": name, "version": version,
            }).get("model_version")
            if not isinstance(record, dict) or record.get("name") != name:
                raise HTTPException(status_code=404, detail="Model version not found")
            client.post("model-versions/set-tag", {
                "name": name, "version": version, "key": "sos.archived",
                "value": "true" if body.archived else "false",
            })
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"name": name, "version": version, "archived": body.archived}


    @router.delete("/api/ml/registry/groups/{registry_key}", tags=["Models"])
    def delete_group(registry_key: str, actor: Actor = MANAGE):
        if registry_key == "default":
            raise HTTPException(status_code=409, detail="Default model group cannot be deleted")
        name = _name(actor.id, registry_key)
        with store.connection() as db:
            in_use = db.execute(text(
                "SELECT count(*) FROM model_revisions "
                "WHERE owner_actor=:actor AND registry_key=:key AND lifecycle_state!='Deleted'"
            ), {"actor": actor.id, "key": registry_key}).scalar_one()
            if in_use:
                raise HTTPException(status_code=409, detail="Model group still has managed versions")
        client = _client()
        try:
            _get_group(client, name)
            versions = client.get("model-versions/search", {
                "filter": f"name = '{name}'", "max_results": 1,
            }).get("model_versions") or []
            if versions:
                raise HTTPException(status_code=409, detail="Delete model versions first")
            client.post("registered-models/delete", {"name": name})
        except MLflowDeliveryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"registry_key": registry_key, "deleted": True}

    return router
