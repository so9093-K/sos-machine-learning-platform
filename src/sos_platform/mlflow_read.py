"""MLflow 연구 기록을 SOS Actor 접근 계약에 맞춰 제한해 노출한다."""

from .mlflow_errors import MLflowDeliveryError
from .mlflow_experiments import (
    DEFAULT_EXPERIMENT,
    experiment_prefix,
    find_experiment,
)


def _page_token(result: dict) -> str | None:
    token = result.get("next_page_token")
    if token is None or token == "":
        return None
    if not isinstance(token, str):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    return token


def read_experiments(client, actor_id: str, *, page_token: str | None = None) -> dict:
    """Actor 이름 공간의 Experiment를 이름/표시 제목과 함께 페이지 단위 조회한다."""
    prefix = experiment_prefix(actor_id)
    request = {
        "filter": f"name LIKE '{prefix}-%'",
        "max_results": 100,
    }
    if page_token:
        request["page_token"] = page_token
    result = client.post("experiments/search", request)
    records = result.get("experiments") or []
    if not isinstance(records, list):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    items = []
    for experiment in records:
        if not isinstance(experiment, dict) or not str(experiment.get("name", "")).startswith(prefix + "-"):
            continue
        tags = experiment.get("tags") or []
        if not isinstance(tags, list):
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        title = next((
            tag.get("value") for tag in tags if isinstance(tag, dict)
            and tag.get("key") == "sos.title"
        ), DEFAULT_EXPERIMENT)
        identifier = experiment.get("experiment_id")
        if isinstance(title, str) and isinstance(identifier, str) and identifier.isdigit():
            items.append({"id": identifier, "name": title})
    return {"items": items, "next_page_token": _page_token(result)}


def _actor_experiment_id(client, actor_id: str, experiment_id: str | None) -> str | None:
    if experiment_id is None:
        default = find_experiment(client, actor_id)
        return default.get("experiment_id") if default else None
    try:
        experiment = client.get(
            "experiments/get", {"experiment_id": experiment_id}
        ).get("experiment")
    except MLflowDeliveryError as exc:
        if str(exc) == "SOS-MLFLOW-HTTP-404-experiments_get":
            raise MLflowDeliveryError("SOS-MLFLOW-EXPERIMENT-NOT-FOUND") from exc
        raise
    if not isinstance(experiment, dict) or (
        experiment.get("experiment_id") != experiment_id or not isinstance(experiment.get("name"), str)
        or not experiment["name"].startswith(experiment_prefix(actor_id) + "-")
    ):
        raise MLflowDeliveryError("SOS-MLFLOW-EXPERIMENT-NOT-FOUND")
    return experiment_id


def read_runs(
    client, store, actor_id: str, *, experiment_id: str | None = None,
    page_token: str | None = None,
) -> dict:
    """현재 Actor의 연구 Experiment에서 Run을 조회하고 Job 소유권을 다시 확인한다."""
    selected_id = _actor_experiment_id(client, actor_id, experiment_id)
    if selected_id is None:
        return {"items": [], "next_page_token": None}
    request = {
        "experiment_ids": [selected_id],
        "max_results": 100,
        "order_by": ["attributes.start_time DESC"],
    }
    if page_token:
        request["page_token"] = page_token
    result = client.post("runs/search", request)
    runs = result.get("runs") or []
    if not isinstance(runs, list):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    visible = []
    for entry in runs:
        if not isinstance(entry, dict):
            continue
        info = entry.get("info") or {}
        data = entry.get("data") or {}
        tags = {
            item.get("key"): item.get("value")
            for item in data.get("tags", []) if isinstance(item, dict)
        }
        job_id = tags.get("sos.job_id")
        job = store.get_job(job_id) if isinstance(job_id, str) else None
        if not job or job.get("owner_actor") != actor_id:
            continue
        visible.append({
            "id": info.get("run_id"), "job_id": job_id,
            "job_kind": job["kind"], "status": info.get("status"),
            "start_time": info.get("start_time"),
            "params": {
                item["key"]: item["value"] for item in data.get("params", [])
                if isinstance(item, dict) and isinstance(item.get("key"), str)
            },
            "metrics": {
                item["key"]: item["value"] for item in data.get("metrics", [])
                if isinstance(item, dict) and isinstance(item.get("key"), str)
            },
        })
    return {"items": visible, "next_page_token": _page_token(result)}
