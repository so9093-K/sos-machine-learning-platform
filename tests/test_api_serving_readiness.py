"""Dashboard 가용성과 실제 추론을 분리하면서 다른 판본·시작 지문은 거부한다."""

from copy import deepcopy
from types import SimpleNamespace

from fastapi.testclient import TestClient

from sos_platform import api_serving
from sos_platform.api import create_app
from sos_platform.ml_outputs import ISOLATION_FOREST_OUTPUT
from sos_platform.security import IdentitySettings
from sos_platform.serving_client import ServingClient, ServingUnavailable, application_config
from sos_platform.store import Store


def test_predict_obeys_runtime_readiness_and_replica_digest(tmp_path, monkeypatch):
    model = {"id": "model-a", "algorithm": "isolation_forest", "features": ["x"],
             "runtime": {"feature_dtypes": ["Float64"]}}
    endpoint = {"id": "endpoint-a", "name": "activity", "desired_state": "serving",
                "active_model_revision_id": model["id"], "pending_model_revision_id": "model-b",
                "previous_model_revision_id": None, "owner_actor": "local-admin",
                "created_at": "2026-10-30T00:00:00Z", "updated_at": "2026-10-30T00:00:00Z"}
    desired = application_config(endpoint["id"], model)
    runtime = {desired["name"]: {"status": "RUNNING", "deployed_app_config": deepcopy(desired)}}
    answer = {"model_revision_id": model["id"], "digest": desired["args"]["digest"],
              **ISOLATION_FOREST_OUTPUT.record(), "outputs": [{"anomaly_score": .4}]}
    calls = []

    def predict(*args):
        calls.append(args)
        return answer

    dashboard_reads = []

    def applications():
        dashboard_reads.append(True)
        return runtime

    client = SimpleNamespace(applications=applications, predict=predict, probe=lambda *_: answer)
    client.is_ready = lambda *args: ServingClient.is_ready(client, *args)
    monkeypatch.setattr(ServingClient, "from_env", lambda: client)
    monkeypatch.setattr(api_serving, "management_client", lambda: None)
    monkeypatch.setattr(api_serving, "verify_registered_version", lambda *_: None)
    monkeypatch.setattr(Store, "get_serving_endpoint", lambda *_: endpoint)
    monkeypatch.setattr(Store, "get_model_revision", lambda *_: model)
    monkeypatch.setattr(Store, "serving_applications", lambda *_: [
        {"endpoint_id": endpoint["id"], "revision": model}])
    api = TestClient(create_app(tmp_path, identity=IdentitySettings("development", {})))
    path = "/api/serving/endpoints/endpoint-a"
    body = {"inputs": [{"x": 2.5}]}
    assert api.get(path).json()["ready"] is True
    result = api.post(path + "/predict", json=body)
    assert result.status_code == 200
    assert "digest" not in result.json()  # 내부 복제본 검증 정보는 공개 추론 계약이 아니다.
    assert len(calls) == 1
    assert len(dashboard_reads) == 1

    runtime[desired["name"]]["deployed_app_config"]["args"]["digest"] = "external-change"
    answer["digest"] = "external-change"
    state = api.get(path).json()
    assert state["ready"] is False
    assert state["runtime"]["active"]["status"] == "OUT_OF_SYNC"
    assert api.post(path + "/predict", json=body).status_code == 503
    assert len(calls) == 2

    runtime[desired["name"]]["deployed_app_config"] = deepcopy(desired)
    runtime[desired["name"]]["status"] = "DEPLOYING"
    answer["digest"] = desired["args"]["digest"]
    assert api.get(path).json()["ready"] is True
    assert api.post(path + "/predict", json=body).status_code == 200
    assert len(calls) == 3
    runtime[desired["name"]]["status"] = "RUNNING"
    # Dashboard가 일치해도 교체 중 남아 있는 이전 설정의 복제본 응답은 전달하지 않는다.
    answer["digest"] = "old-replica"
    assert api.post(path + "/predict", json=body).status_code == 503
    assert len(calls) == 4
    answer["digest"] = desired["args"]["digest"]
    assert api.post(path + "/predict", json=body).status_code == 200

    def unavailable(*_):
        raise ServingUnavailable("offline")

    client.applications = unavailable
    state = api.get(path).json()
    assert state["ready"] is True
    assert state["runtime"]["reachable"] is False
    assert api.post(path + "/predict", json=body).status_code == 200
    assert len(calls) == 6
    client.probe = unavailable
    client.predict = unavailable
    assert api.get(path).json()["ready"] is False
    assert api.post(path + "/predict", json=body).status_code == 503
    endpoint["desired_state"] = "stopped"
    assert api.post(path + "/predict", json=body).status_code == 409
