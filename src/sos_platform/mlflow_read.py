"""MLflow 연구 기록을 SOS Actor 접근 계약에 맞춰 제한해 노출한다."""

from .mlflow_errors import MLflowDeliveryError
from .mlflow_experiments import find_experiment
from .mlflow_registry import registered_model_name


def read_experiments(client, actor_id: str) -> list[dict]:
    """현재 Actor가 소유한 연구 Experiment만 반환한다."""
    experiment = find_experiment(client, actor_id)
    if experiment is None:
        return []
    return [{
        "id": experiment["experiment_id"],
        "name": "내 ML 실험",
    }]


def read_runs(client, store, actor_id: str) -> list[dict]:
    """외부 Run 목록에 SOS Job 소유권 검사를 다시 적용한다."""
    experiment = find_experiment(client, actor_id)
    if experiment is None:
        return []
    experiment_id = experiment.get("experiment_id")
    if not isinstance(experiment_id, str):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    result = client.post("runs/search", {
        "experiment_ids": [experiment_id],
        "max_results": 100,
        "order_by": ["attributes.start_time DESC"],
    })
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
    return visible


def read_model_versions(client, store, actor_id: str) -> list[dict]:
    """MLflow Registry가 소유한 버전을 조회하되 실행 ID는 SOS가 검증한다."""
    name = registered_model_name({
        "owner_actor": actor_id, "algorithm": "isolation_forest",
    })
    versions = client.get("model-versions/search", {
        "filter": f"name = '{name}'", "max_results": 100,
    }).get("model_versions") or []
    if not isinstance(versions, list):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    visible = []
    for version in versions:
        if not isinstance(version, dict) or version.get("name") != name:
            continue
        tags = {
            tag.get("key"): tag.get("value")
            for tag in version.get("tags") or [] if isinstance(tag, dict)
        }
        job_id = tags.get("sos.job_id")
        job = store.get_job(job_id) if isinstance(job_id, str) else None
        if not job or job.get("owner_actor") != actor_id:
            continue
        model_id = (job.get("result") or {}).get("model_revision_id")
        model = store.get_model_revision(model_id) if model_id else None
        if (
            not model or model.get("owner_actor") != actor_id
            or model.get("registry_model") != name
            or model.get("registry_version") != version.get("version")
            or model.get("sha256") != tags.get("sos.sha256")
            or model.get("location") != version.get("source")
            or version.get("status") != "READY"
        ):
            continue
        visible.append({
            "name": name, "version": version["version"],
            "model_id": model_id, "run_id": version.get("run_id"),
            "status": version.get("status"), "job_id": job_id,
            "aliases": version.get("aliases") or [],
        })
    return visible
