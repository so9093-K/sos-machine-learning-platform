"""연구 목적별 MLflow Experiment 식별과 Actor 소유권 확인.

실험 메타데이터의 정본은 MLflow이며 SOS에 별도 연구 관리 테이블을 만들지 않는다.
"""

import hashlib

from .mlflow_errors import MLflowDeliveryError

DEFAULT_EXPERIMENT = "기본 실험"


def experiment_prefix(owner: str) -> str:
    """Actor 식별자 원문을 MLflow 이름으로 보내지 않는다."""
    if not isinstance(owner, str) or not owner:
        raise MLflowDeliveryError("SOS-MLFLOW-OWNER-INVALID")
    return "sos-research-" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]


def experiment_name(owner: str, title: str = DEFAULT_EXPERIMENT) -> str:
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        raise MLflowDeliveryError("SOS-MLFLOW-EXPERIMENT-INVALID")
    normalized = title.strip()
    return experiment_prefix(owner) + "-" + hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()[:16]


def find_experiment(client, owner: str, title: str = DEFAULT_EXPERIMENT) -> dict | None:
    """연구 제목에서 파생된 고정 이름을 정확히 검색한다."""
    name = experiment_name(owner, title)
    result = client.post("experiments/search", {
        "filter": f"name = '{name}'", "max_results": 100,
    })
    entries = result.get("experiments") or []
    if not isinstance(entries, list):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    found = [item for item in entries if isinstance(item, dict) and item.get("name") == name]
    if len(found) > 1:
        raise MLflowDeliveryError("SOS-MLFLOW-DUPLICATE-EXPERIMENTS")
    return found[0] if found else None


def ensure_experiment(client, owner: str, title: str = DEFAULT_EXPERIMENT) -> str:
    """연구 묶음이 없다면 생성하고, 동일 제목은 기존 Experiment를 사용한다."""
    item = find_experiment(client, owner, title)
    identifier = (
        item.get("experiment_id") if item else client.post(
            "experiments/create", {
                "name": experiment_name(owner, title),
                "tags": [
                    {"key": "sos.source", "value": "sos-platform"},
                    {"key": "sos.title", "value": title.strip()},
                    # 이름은 Actor를 가린 조회 키이므로 MLflow 목록에는 설명으로 연구 제목을 보여준다.
                    {"key": "mlflow.note.content", "value": f"SOS 연구 묶음 · {title.strip()}"},
                ],
            },
        ).get("experiment_id")
    )
    if not isinstance(identifier, str) or not identifier.isdigit():
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    return identifier
