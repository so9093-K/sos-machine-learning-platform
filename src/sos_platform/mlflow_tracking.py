"""MLflow REST Tracking 어댑터: 선택적이고 재시도 가능한 외부 투영.

MLflow 서버를 SOS의 모델/Job 정본으로 사용하지 않는다. 모델 바이트,
원시 Feature, 개인 데이터, 토큰은 외부로 전송하지 않는다.
"""

import json
import math
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .mlflow_config import MLflowSettings


class MLflowDeliveryError(Exception):
    """외부 전송 실패의 원문이나 원격 응답을 노출하지 않는다."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class MLflowClient:
    """운영자가 명시한 한 MLflow 서버로만 제한된 JSON을 보낸다."""

    def __init__(self, settings: MLflowSettings):
        self.settings = settings
        self._opener = build_opener(_NoRedirect())

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
        for key, value in sorted(job["spec"].get("params", {}).items()):
            if type(value) in (str, int, float, bool):
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


def run_tracking_once(store, client: MLflowClient) -> bool:
    """SOS 정본과 독립적으로 전송 한 건만 처리한다."""
    export = store.claim_tracking_export()
    if export is None:
        return False
    job_id = export["job_id"]
    try:
        job = store.get_job(job_id)
        if job is None:
            raise MLflowDeliveryError("SOS-MLFLOW-SOURCE-MISSING")
        send_to_mlflow(
            client,
            job,
            known_run_id=export["run_id"],
            remember_run=lambda value: store.save_tracking_run_id(job_id, value),
        )
    except MLflowDeliveryError as exc:
        store.finish_tracking_export(job_id, error_code=str(exc))
    except Exception:
        # 외부 전송 장애로 ML Job 상태를 변경하거나 자격 증명을 기록하지 않는다.
        store.finish_tracking_export(job_id, error_code="SOS-MLFLOW-UNAVAILABLE")
    else:
        store.finish_tracking_export(job_id)
    return True
