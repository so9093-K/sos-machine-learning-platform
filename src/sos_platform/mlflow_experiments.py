"""MLflow Experiment는 연구 묶음이고 SOS Job/Attempt는 실행의 정본이다."""

import hashlib

from .mlflow_errors import MLflowDeliveryError


def experiment_name(owner: str) -> str:
    """Actor별 연구 영역 이름에 Actor ID 원문을 사용하지 않는다."""
    if not isinstance(owner, str) or not owner:
        raise MLflowDeliveryError("SOS-MLFLOW-OWNER-INVALID")
    return "sos-research-" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]


def find_experiment(client, owner: str) -> dict | None:
    """이름으로 정확하게 검색하며 실험 정보의 소유 경계를 검증한다."""
    name = experiment_name(owner)
    result = client.post("experiments/search", {
        "filter": f"name = '{name}'", "max_results": 1000,
    })
    entries = result.get("experiments") or []
    if not isinstance(entries, list):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    found = [item for item in entries if isinstance(item, dict) and item.get("name") == name]
    if len(found) > 1:
        raise MLflowDeliveryError("SOS-MLFLOW-DUPLICATE-EXPERIMENTS")
    return found[0] if found else None


def ensure_experiment(client, owner: str) -> str:
    """첫 실행에서 생성하고 재시도 시 동일한 Experiment를 이용한다."""
    item = find_experiment(client, owner)
    identifier = (
        item.get("experiment_id") if item else client.post(
            "experiments/create", {
                "name": experiment_name(owner),
                "tags": [{"key": "sos.source", "value": "sos-platform"}],
            },
        ).get("experiment_id")
    )
    if not isinstance(identifier, str) or not identifier.isdigit():
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    return identifier
