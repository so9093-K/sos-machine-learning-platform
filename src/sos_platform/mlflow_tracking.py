"""MLflow 연구 기록 및 Registry 관리 어댑터.

모델 자산 이름·버전은 MLflow에서 관리한다. SOS 실행·무결성 근거는
SOS가 소유하며 모델 바이트, 원시 Feature, 비밀정보는 전송하지 않는다.
"""

import hashlib
import json
import math
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import WorkerOwnershipLost
from .mlflow_config import MLflowSettings
from .mlflow_errors import MLflowDeliveryError
from .mlflow_registry import register_model_version


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class MLflowClient:
    """운영자가 명시한 한 MLflow 서버로만 제한된 JSON을 보낸다."""

    def __init__(self, settings: MLflowSettings):
        self.settings = settings
        self._opener = build_opener(_NoRedirect())

    def get(self, endpoint: str, params: dict) -> dict:
        """GET 응답도 POST와 동일하게 리다이렉트·크기·대기 시간을 제한한다."""
        req = Request(
            self.settings.endpoint + "/api/2.0/mlflow/" + endpoint + "?" + urlencode(params),
            headers={"Authorization": f"Bearer {self.settings.token}"} if self.settings.token else {},
            method="GET",
        )
        try:
            with self._opener.open(req, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            return result
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-UNAVAILABLE") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID") from exc

    def post(self, endpoint: str, payload: dict) -> dict:
        """HTTP 리다이렉트를 거부하고 응답 크기·대기 시간을 제한한다."""
        data = json.dumps(payload, allow_nan=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        req = Request(
            self.settings.endpoint + "/api/2.0/mlflow/" + endpoint,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            return parsed
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-UNAVAILABLE") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID") from exc


def _numeric_metrics(job: dict) -> list[dict]:
    """수치형·유한한 지표만 내보낸다. 미정의 지표는 생략한다."""
    result = job.get("result") or {}
    keys = {
        "ml_train": "training_score_summary",
        "ml_score": "score_summary",
        "ml_policy": "decision_summary",
        "ml_evaluate": "decision_metrics",
    }
    source = result.get(keys.get(job["kind"], ""), {})
    if not isinstance(source, dict):
        return []
    now_ms = int(time.time() * 1000)
    return [
        {"key": f"sos.{key}", "value": float(value), "timestamp": now_ms, "step": 0}
        for key, value in sorted(source.items())
        if type(value) in (int, float) and math.isfinite(value)
    ]


def send_to_mlflow(
    client: MLflowClient,
    job: dict,
    *,
    known_run_id: str | None,
    remember_run: Callable[[str], None],
) -> str:
    """Sos Job ID를 외부 Run 검색 키로 사용해 중단 후 재전송한다.

    응답이 소실된 신규 Run 생성은 전송 전 검색으로 최대한 복구하나,
    MLflow 서버의 비원자적 search/create 때문에 완전한 exactly-once는 보장하지 않는다.
    """
    if job["status"] != "Succeeded" or not job["kind"].startswith("ml_"):
        raise ValueError("Only successful ML jobs can be tracked")
    experiment = client.settings.experiment_id
    run_id = known_run_id
    if run_id is None:
        # 입력 Job ID는 SOS가 발급한 UUID이며 SQL/검색 필터의 사용자 문자열이 아니다.
        found = client.post(
            "runs/search",
            {
                "experiment_ids": [experiment],
                "filter": f"tags.`sos.job_id` = '{job['id']}'",
                "max_results": 2,
            },
        ).get("runs", [])
        if not isinstance(found, list):
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        if len(found) > 1:
            raise MLflowDeliveryError("SOS-MLFLOW-DUPLICATE-RUNS")
        if found:
            run_id = found[0].get("info", {}).get("run_id")
        else:
            created = client.post(
                "runs/create",
                {
                    "experiment_id": experiment,
                    "run_name": f"sos-{job['kind']}-{job['id'][:8]}",
                    "tags": [
                        {"key": "sos.job_id", "value": job["id"]},
                        {"key": "sos.job_kind", "value": job["kind"]},
                        {"key": "sos.dataset_id", "value": job["dataset_id"]},
                    ],
                },
            )
            run_id = created.get("run", {}).get("info", {}).get("run_id")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        remember_run(run_id)
    params = []
    if job["kind"] == "ml_train":
        # 모델 설정은 공개 가능한 필드만 전송한다. 향후 Adapter 확장으로
        # 비밀값·경로·사용자 입력이 추가돼도 자동으로 외부에 유출되지 않는다.
        supported = {"n_estimators", "max_samples", "max_features", "bootstrap", "random_state"}
        for key, value in sorted(job["spec"].get("params", {}).items()):
            if key in supported and type(value) in (str, int, float, bool):
                params.append({"key": f"sos.{key}", "value": str(value)})
    client.post(
        "runs/log-batch",
        {
            "run_id": run_id,
            "metrics": _numeric_metrics(job),
            "params": params,
            "tags": [{"key": "sos.source", "value": "sos-platform"}],
        },
    )
    client.post("runs/update", {"run_id": run_id, "status": "FINISHED"})
    return run_id


def _destination_fingerprint(settings: MLflowSettings) -> str:
    """인증정보를 제외한 Tracking 서버와 Experiment의 연결 지문."""
    identity = f"{settings.endpoint}\0{settings.experiment_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def run_tracking_once(store, client: MLflowClient) -> bool:
    """SOS 정본과 독립적으로 전송 한 건만 처리한다."""
    export = store.claim_tracking_export()
    if export is None:
        return False
    job_id = export["job_id"]
    try:
        if not store.bind_tracking_destination(job_id, _destination_fingerprint(client.settings)):
            raise MLflowDeliveryError("SOS-MLFLOW-TARGET-CHANGED")
        job = store.get_job(job_id)
        if job is None:
            raise MLflowDeliveryError("SOS-MLFLOW-SOURCE-MISSING")
        run_id = send_to_mlflow(
            client,
            job,
            known_run_id=export["run_id"],
            remember_run=lambda value: store.save_tracking_run_id(job_id, value),
        )
        if job["kind"] == "ml_train":
            model_id = (job.get("result") or {}).get("model_revision_id")
            model = store.get_model_revision(model_id) if model_id else None
            if model is None:
                raise MLflowDeliveryError("SOS-MLFLOW-MODEL-INVALID")
            name, version = register_model_version(client, model, run_id)
            store.save_registry_version(model_id, name, version)
    except MLflowDeliveryError as exc:
        store.finish_tracking_export(job_id, error_code=str(exc))
    except WorkerOwnershipLost:
        # DB 세션 잠금을 잃으면 새 Worker의 복구 판단에 맡긴다.
        raise
    except Exception:
        # 외부 전송 장애로 ML Job 상태를 변경하거나 자격 증명을 기록하지 않는다.
        store.finish_tracking_export(job_id, error_code="SOS-MLFLOW-UNAVAILABLE")
    else:
        store.finish_tracking_export(job_id)
    return True
