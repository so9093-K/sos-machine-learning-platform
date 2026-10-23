"""MLflow Registry 연결: 모델 자산 관리와 SOS 실행 아티팩트의 경계.

MLflow가 논리적 모델 이름과 버전을 발급한다. SOS는 기존 Worker가 발급한
불변 아티팩트 URI와 SHA-256, Runtime 계약을 계속 검사한다.
"""

import hashlib
import re
from urllib.parse import urlencode

from .mlflow_tracking import MLflowDeliveryError


def registered_model_name(model: dict) -> str:
    """Actor 원문을 Registry 이름에 노출하지 않는 안정적인 이름."""
    owner = model.get("owner_actor")
    algorithm = model.get("algorithm")
    if not isinstance(owner, str) or not owner:
        raise MLflowDeliveryError("SOS-MLFLOW-MODEL-INVALID")
    if algorithm != "isolation_forest":
        raise MLflowDeliveryError("SOS-MLFLOW-MODEL-INVALID")
    return "sos-if-" + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]


def _tags(version: dict) -> dict[str, str]:
    values = version.get("tags") or []
    if not isinstance(values, list):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    return {
        item["key"]: item["value"]
        for item in values
        if isinstance(item, dict) and isinstance(item.get("key"), str)
        and isinstance(item.get("value"), str)
    }


def _versions(client, name: str):
    """페이지를 끝까지 읽어 재시도 후 중복 등록을 피한다."""
    token = None
    seen = set()
    while True:
        params = {"filter": f"name = '{name}'", "max_results": 1000}
        if token:
            params["page_token"] = token
        result = client.get("model-versions/search", params)
        versions = result.get("model_versions") or []
        if not isinstance(versions, list):
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        for version in versions:
            if not isinstance(version, dict):
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            if version.get("name") == name:
                yield version
        token = result.get("next_page_token")
        if not token:
            return
        if not isinstance(token, str) or token in seen:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        seen.add(token)


def register_model_version(client, model: dict, run_id: str) -> tuple[str, str]:
    """한 학습 Job의 단일 Registry 판본을 검색 후 생성한다.

    MLflow의 search/create는 원자적이지 않지만 SOS의 단일 Worker 소유권과
    재시도 시 전체 검색을 조합한다. 중복이 발견되면 임의 판본을 선택하지 않는다.
    """
    name = registered_model_name(model)
    digest = model.get("sha256")
    source = model.get("location")
    job_id = model.get("job_id")
    if (
        not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
        or not isinstance(source, str) or not source.startswith("s3://")
        or not isinstance(job_id, str) or not job_id
    ):
        raise MLflowDeliveryError("SOS-MLFLOW-MODEL-INVALID")
    existing = []
    for version in _versions(client, name):
        tags = _tags(version)
        if tags.get("sos.job_id") == job_id:
            existing.append(version)
    if len(existing) > 1:
        raise MLflowDeliveryError("SOS-MLFLOW-DUPLICATE-MODELS")
    if existing:
        item = existing[0]
        if (
            item.get("source") != source or _tags(item).get("sos.sha256") != digest
            or item.get("run_id") != run_id
        ):
            raise MLflowDeliveryError("SOS-MLFLOW-MODEL-MISMATCH")
    else:
        # 한 Worker만 등록한다. 이미 같은 이름이 있으면 생성하지 않는다.
        models = client.get("registered-models/search", {"max_results": 1000}).get(
            "registered_models", []
        )
        if not isinstance(models, list):
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        if not any(item.get("name") == name for item in models if isinstance(item, dict)):
            client.post("registered-models/create", {
                "name": name,
                "tags": [{"key": "sos.source", "value": "sos-platform"}],
            })
        result = client.post("model-versions/create", {
            "name": name, "source": source, "run_id": run_id,
            "tags": [
                {"key": "sos.job_id", "value": job_id},
                {"key": "sos.sha256", "value": digest},
            ],
        })
        item = result.get("model_version")
    if not isinstance(item, dict):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    version = item.get("version")
    if not isinstance(version, str) or not re.fullmatch("[1-9][0-9]*", version):
        raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
    return name, version
