"""MLflow 연구 기록 및 Registry 관리 어댑터.

모델 자산 이름·버전은 MLflow에서 관리한다. SOS 실행·무결성 근거는
SOS가 소유하며 모델 바이트, 원시 Feature, 비밀정보는 전송하지 않는다.
"""

import hashlib
import json
import logging
import math
import time
from collections.abc import Callable
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import WorkerOwnershipLost
from .mlflow_config import MLflowSettings
from .mlflow_errors import MLflowDeliveryError
from .mlflow_experiments import DEFAULT_EXPERIMENT, ensure_experiment
from .mlflow_registry import register_model_version, verify_registered_version


logger = logging.getLogger(__name__)


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
        except HTTPError as exc:
            # 원격 응답 본문·URL·토큰은 기록하지 않고 명시적 API 경계만 식별한다.
            code = endpoint.replace("/", "_")
            raise MLflowDeliveryError(f"SOS-MLFLOW-HTTP-{exc.code}-{code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-UNAVAILABLE") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID") from exc

    def post(self, endpoint: str, payload: dict, *, method: str = "POST") -> dict:
        """HTTP 리다이렉트를 거부하고 응답 크기·대기 시간을 제한한다."""
        data = json.dumps(payload, allow_nan=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.settings.token:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        req = Request(
            self.settings.endpoint + "/api/2.0/mlflow/" + endpoint,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(req, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            parsed = json.loads(raw) if raw else ({} if method == "DELETE" else None)
            if not isinstance(parsed, dict):
                raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
            return parsed
        except HTTPError as exc:
            # 원격 응답 본문·URL·토큰은 기록하지 않고 명시적 API 경계만 식별한다.
            code = endpoint.replace("/", "_")
            raise MLflowDeliveryError(f"SOS-MLFLOW-HTTP-{exc.code}-{code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-UNAVAILABLE") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID") from exc


    def patch(self, endpoint: str, payload: dict) -> dict:
        """MLflow REST PATCH 계약의 JSON 요청."""
        return self.post(endpoint, payload, method="PATCH")

    def delete(self, endpoint: str, payload: dict) -> dict:
        """MLflow REST DELETE 계약의 JSON 요청. 빈 응답도 허용한다."""
        return self.post(endpoint, payload, method="DELETE")


def _unix_millis(value: object) -> int | None:
    """명시적으로 타임존을 가진 SOS 기록 시각만 Unix 밀리초로 변환한다."""
    try:
        if isinstance(value, datetime):
            timestamp = value
        elif isinstance(value, str):
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return None
        millis = int(timestamp.timestamp() * 1000)
        return millis if millis > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _job_event_times(job: dict) -> tuple[int | None, int | None]:
    """마지막 실제 Attempt의 시작·종료 시각을 우선 사용한다.

    Attempt가 없는 즉시 취소 등은 Job 접수·갱신 시각을 기준으로 기록한다.
    외부 등록 Retry 당시 현재 시각을 학습 시각으로 가장하지 않는다.
    """
    attempts = job.get("attempts") or []
    attempt = attempts[-1] if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict) else {}
    start = _unix_millis(attempt.get("started_at")) or _unix_millis(job.get("created_at"))
    end = _unix_millis(attempt.get("finished_at")) or _unix_millis(job.get("updated_at"))
    if start is not None and end is not None and end < start:
        return start, None
    return start, end


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
    _, finished_ms = _job_event_times(job)
    now_ms = finished_ms or int(time.time() * 1000)
    return [
        {"key": f"sos.{key}", "value": float(value), "timestamp": now_ms, "step": 0}
        for key, value in sorted(source.items())
        if type(value) in (int, float) and math.isfinite(value)
    ]


def _job_experiment_title(store, job: dict) -> str:
    """같은 Actor의 원본 학습으로 거슬러 올라가 연구 묶음을 연결한다."""
    if job["kind"] == "ml_train":
        return job["spec"].get("experiment_title") or DEFAULT_EXPERIMENT
    spec = job.get("spec") or {}
    model_id = spec.get("model_revision_id")
    if job["kind"] in ("ml_policy", "ml_evaluate", "ml_explain"):
        score_id = spec.get("scoring_result_id")
        score = store.get_scoring_result(score_id) if score_id else None
        model_id = score.get("model_revision_id") if score else None
    if not model_id:
        return DEFAULT_EXPERIMENT
    model = store.get_model_revision(model_id)
    training_job = store.get_job(model["job_id"]) if model else None
    if (
        not training_job or training_job.get("kind") != "ml_train"
        or training_job.get("owner_actor") != job.get("owner_actor")
    ):
        return DEFAULT_EXPERIMENT
    return training_job["spec"].get("experiment_title") or DEFAULT_EXPERIMENT


def send_to_mlflow(
    client: MLflowClient,
    job: dict,
    *,
    known_run_id: str | None,
    remember_run: Callable[[str], None],
    experiment_title: str | None = None,
) -> str:
    """Sos Job ID를 외부 Run 검색 키로 사용해 중단 후 재전송한다.

    응답이 소실된 신규 Run 생성은 전송 전 검색으로 최대한 복구하나,
    MLflow 서버의 비원자적 search/create 때문에 완전한 exactly-once는 보장하지 않는다.
    """
    if job["status"] not in ("Succeeded", "Failed", "Cancelled") or not job["kind"].startswith("ml_"):
        raise ValueError("Only terminal ML jobs can be tracked")
    run_id = known_run_id
    if run_id is None:
        experiment = ensure_experiment(
            client, job["owner_actor"], experiment_title or (
                job.get("spec", {}).get("experiment_title") or DEFAULT_EXPERIMENT
            )
        )
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
            started_ms, _ = _job_event_times(job)
            create_payload = {
                "experiment_id": experiment,
                "run_name": f"sos-{job['kind']}-{job['id'][:8]}",
                "tags": [
                    {"key": "sos.job_id", "value": job["id"]},
                    {"key": "sos.job_kind", "value": job["kind"]},
                    {"key": "sos.dataset_id", "value": job["dataset_id"]},
                ],
            }
            if started_ms is not None:
                create_payload["start_time"] = started_ms
            created = client.post("runs/create", create_payload)
            run_id = created.get("run", {}).get("info", {}).get("run_id")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
            raise MLflowDeliveryError("SOS-MLFLOW-RESPONSE-INVALID")
        remember_run(run_id)
    # Run 비교에는 모델 설정뿐 아니라 사용한 Dataset 판본이 필요하다.
    # 값은 SOS가 이미 확정한 실행 메타데이터에서만 가져온다.
    params = [
        {"key": "sos.dataset_id", "value": job["dataset_id"]},
        {"key": "sos.job_kind", "value": job["kind"]},
    ]
    dataset_version = job["spec"].get("dataset_version")
    if isinstance(dataset_version, str) and dataset_version:
        params.append({"key": "sos.dataset_version", "value": dataset_version})
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
    _, finished_ms = _job_event_times(job)
    update_payload = {
        "run_id": run_id,
        "status": {
            "Succeeded": "FINISHED", "Failed": "FAILED", "Cancelled": "KILLED",
        }[job["status"]],
    }
    if finished_ms is not None:
        update_payload["end_time"] = finished_ms
    client.post("runs/update", update_payload)
    return run_id


def _destination_fingerprint(settings: MLflowSettings) -> str:
    """인증정보를 제외한 Tracking 서버·Experiment 정책의 연결 지문."""
    identity = f"{settings.endpoint}\0owner-experiment-v1".encode()
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
            experiment_title=_job_experiment_title(store, job),
        )
        if job["kind"] == "ml_train" and job["status"] == "Succeeded":
            model_id = (job.get("result") or {}).get("model_revision_id")
            model = store.get_model_revision(model_id) if model_id else None
            if model is None:
                raise MLflowDeliveryError("SOS-MLFLOW-MODEL-INVALID")
            name, version = register_model_version(client, model, run_id)
            # Registry가 실제 READY 상태인지 검증한 뒤에만 SOS 실행 경로를 연다.
            # 생성 직후 비동기 PENDING이면 전송을 Retry로 남기며 학습 결과는 유지한다.
            verify_registered_version(
                client, {**model, "registry_model": name, "registry_version": version}
            )
            store.save_registry_version(model_id, name, version)
    except MLflowDeliveryError as exc:
        store.finish_tracking_export(job_id, error_code=str(exc))
    except WorkerOwnershipLost:
        # DB 세션 잠금을 잃으면 새 Worker의 복구 판단에 맡긴다.
        raise
    except Exception as exc:
        # 프로그래밍 오류를 외부 서버 장애로 숨기지 않는다. 예외 메시지는
        # 원격 URI·비밀정보를 포함할 수 있어 로그에 기록하지 않는다.
        logger.error(
            "MLflow 등록 내부 오류: job=%s exception=%s",
            job_id, type(exc).__name__,
        )
        store.finish_tracking_export(job_id, error_code="SOS-MLFLOW-INTERNAL")
    else:
        store.finish_tracking_export(job_id)
    return True
