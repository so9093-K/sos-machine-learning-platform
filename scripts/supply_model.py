"""활동 이상 탐지 모델을 SOS에 공급하고 선택적으로 온라인 서빙에 배포한다.

모델은 두 경로로 공급한다. Dataset으로 SOS에서 학습하거나(--dataset-file·--dataset-id), 외부 Pipeline이
생산해 MLflow에 등록한 고정 모델 버전을 연결한다(--mlflow-model·--mlflow-version).
CLI로 연결할 생산 Run은 connect_to_sos=false로 실행한다. Dagster에서 이미 SOS에 연결했다면
새로 연결하지 않고 그 Model Revision의 상세에서 제공·배포한다.
같은 --state-file로 재실행하면 등록 Dataset·학습 또는 연결 요청·멱등성 키를 재사용한다.
상태 파일에는 토큰을 저장하지 않는다. 새 공급은 별도의 상태 파일로 명시한다.
"""

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

import httpx

TOPIC_ID = "activity_anomaly"
TERMINAL = {"Succeeded", "Failed", "Cancelled"}
PARAMS = {
    "n_estimators": 100,
    "max_samples": "auto",
    "max_features": 1.0,
    "bootstrap": False,
    "random_state": 42,
}


def fail(message: str) -> None:
    print(f"공급 실패: {message}", file=sys.stderr)
    raise SystemExit(1)


def checked(response: httpx.Response) -> dict | list[dict]:
    if response.is_error:
        try:
            body = response.json()
            detail, code = body.get("detail"), body.get("code")
        except ValueError:
            detail, code = response.text[:300], None
        fail(f"HTTP {response.status_code} {code or ''} {detail}")
    return response.json()


def save_state(path: Path, state: dict) -> None:
    """응답 유실·프로세스 종료 후에도 같은 제출을 식별하도록 원자적으로 기록한다."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def wait(client: httpx.Client, path: str, done: Callable[[dict], bool], *, interval: float,
         timeout: float, label: str) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        item = checked(client.get(path))
        if done(item):
            return item
        if time.monotonic() > deadline:
            fail(f"{label} 대기 시간이 초과됐습니다. 같은 상태 파일로 재개하세요.")
        time.sleep(interval)


def require_capabilities(identity: dict, source: dict) -> None:
    """MLflow 버전 연결은 모델 관리, Dataset 학습은 학습·모델 관리 권한이 필요하다."""
    required = {"model.manage"} if "mlflow_model" in source else {"model.train", "model.manage"}
    if not required.issubset(identity["capabilities"]):
        fail("모델 공급 권한이 있는 관리자 또는 automation 서비스 계정이 필요합니다.")


def supply(client: httpx.Client, args: argparse.Namespace, description: str) -> None:
    """고정된 Dataset 학습 또는 MLflow 버전 연결을 재개하고 Registry 등록을 확인한 판본만 제공한다."""
    identity = checked(client.get("/api/v2/auth/me"))
    path = args.state_file
    api = str(client.base_url).rstrip("/")
    source = ({"file": str(args.dataset_file.resolve())} if args.dataset_file else
              {"id": args.dataset_id} if args.dataset_id else
              {"mlflow_model": args.mlflow_model, "mlflow_version": args.mlflow_version}
              if args.mlflow_model else None)
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("version") != 1
                or state.get("api") != api or state.get("actor_id") != identity["actor_id"]
                or not isinstance(state.get("source"), dict)
                or set(state["source"]) not in ({"file"}, {"id"}, {"mlflow_model", "mlflow_version"})):
            fail("상태 파일의 API·Actor·형식을 확인하세요. 다른 공급에는 새 상태 파일이 필요합니다.")
        # 업로드 응답이 유실된 경우 확인한 Dataset ID를 명시해 복구할 수 있다.
        recovering_upload = (args.dataset_id and state.get("upload_started") and
                             not state.get("dataset_id"))
        if recovering_upload:
            state["source"] = source
        elif source is not None and source != state["source"]:
            fail("상태 파일의 입력과 다릅니다. 새 입력에는 새 상태 파일을 사용하세요.")
        require_capabilities(identity, state["source"])
        training_input = state.get("training_input") or {}
        if args.sample_rows is not None and args.sample_rows != training_input.get("rows"):
            fail("상태 파일의 표본 행 수와 다릅니다.")
        if args.seed is not None and args.seed != training_input.get("seed"):
            fail("상태 파일의 Seed와 다릅니다.")
    else:
        if source is None:
            fail("새 공급에는 --dataset-file, --dataset-id 또는 --mlflow-model이 필요합니다.")
        require_capabilities(identity, source)
        state = {
            "version": 1, "api": api, "actor_id": identity["actor_id"], "source": source,
            "submission_key": str(uuid.uuid4()),
        }
        if "mlflow_model" not in source:
            state["training_input"] = ({"mode": "sample", "rows": args.sample_rows,
                                        "seed": args.seed if args.seed is not None else 42}
                                       if args.sample_rows is not None else {"mode": "all"})
        save_state(path, state)
    model_id = (connect_mlflow_version(client, args, state, path) if "mlflow_model" in state["source"]
                else train_from_dataset(client, args, state, path))
    state["model_id"] = model_id
    save_state(path, state)

    def registered(item: dict) -> bool:
        if item["lifecycle_state"] != "Active":
            fail("삭제 중이거나 삭제된 모델은 제공할 수 없습니다.")
        return bool(item.get("registry_version"))

    model = wait(client, f"/api/ml/models/{model_id}", registered, interval=3,
                 timeout=args.timeout, label="Registry 등록")
    print(f"모델 판본 확인 · {model_id} · Registry v{model['registry_version']}", flush=True)
    offering = checked(client.get(f"/api/ml/models/{model_id}/offering"))
    if offering.get("withdrawn_at") is not None:
        fail("제공이 중단된 판본입니다. 재제공은 제공 API로 명시적으로 요청하세요.")
    if not offering["offered"] or offering.get("description") != description:
        offering = checked(client.put(f"/api/ml/models/{model_id}/offering",
                                      json={"description": description}))
    if not offering["offered"]:
        fail("제공 상태를 확인하지 못했습니다.")
    print(f"제공 완료 · {model_id}")
    if args.endpoint:
        serve(client, args, state, path, model_id)
    web = os.getenv("SOS_WEB_URL", "http://localhost:9601").rstrip("/")
    print(f"활동 이상 탐지 · {web}/topics/activity-anomaly?{urlencode({'model': model_id})}")
    print("Dataset을 선택·등록해 모델을 적용하거나, '값 직접 입력'으로 개별 관측값을 계산하세요.")


def train_from_dataset(client: httpx.Client, args: argparse.Namespace, state: dict,
                       path: Path) -> str:
    """Dataset을 등록·고정하고 주제의 입력 Feature로 SOS 학습을 요청한다."""
    if not state.get("dataset_id"):
        if "file" in state["source"]:
            if state.get("upload_started"):
                fail("파일 등록 응답이 확인되지 않았습니다. GET /api/datasets로 등록 상태를 확인한 뒤 "
                     "같은 --state-file과 --dataset-id로 재개하세요. 파일은 다시 전송하지 않습니다.")
            source_file = Path(state["source"]["file"])
            with source_file.open("rb") as handle:
                state["upload_started"] = True
                save_state(path, state)
                dataset = checked(client.post("/api/datasets/files",
                                  files={"file": (source_file.name, handle)}))
        else:
            dataset = checked(client.get(f"/api/datasets/{state['source']['id']}"))
        state["dataset_id"] = dataset["id"]
        save_state(path, state)
    print(f"학습 Dataset · {state['dataset_id']}", flush=True)

    if not state.get("training_request"):
        topic = next((item for item in checked(client.get("/api/topics"))
                      if item["id"] == TOPIC_ID), None)
        if topic is None:
            fail("활동 이상 탐지 분석 주제를 찾을 수 없습니다.")
        state["training_request"] = {
            "dataset_id": state["dataset_id"], "features": topic["required_features"],
            "params": PARAMS, "training_input": state["training_input"],
            "missing_policy": "train_median", "experiment_title": "제공 모델",
        }
        save_state(path, state)
    if not state.get("job_id"):
        job = checked(client.post("/api/ml/train/jobs", json=state["training_request"],
                      headers={"Idempotency-Key": state["submission_key"]}))
        state["job_id"] = job["id"]
        save_state(path, state)
    print(f"학습 Job · {state['job_id']}", flush=True)
    job = wait(client, f"/api/jobs/{state['job_id']}",
               lambda item: item["status"] in TERMINAL, interval=5,
               timeout=args.timeout, label="학습")
    if job["status"] != "Succeeded":
        fail(f"학습 {job['status']} · {job.get('error_code') or ''} {job.get('error') or ''}")
    return job["result"]["model_revision_id"]


def connect_mlflow_version(client: httpx.Client, args: argparse.Namespace, state: dict,
                           path: Path) -> str:
    """외부 Pipeline이 MLflow에 등록한 고정 모델 버전을 다시 학습하지 않고 SOS 판본으로 연결한다."""
    if not state.get("job_id"):
        job = checked(client.post("/api/ml/models/imports", json=state["source"],
                      headers={"Idempotency-Key": state["submission_key"]}))
        state["job_id"] = job["id"]
        save_state(path, state)
    print(f"연결 Job · {state['job_id']} · MLflow {state['source']['mlflow_model']} "
          f"v{state['source']['mlflow_version']}", flush=True)
    job = wait(client, f"/api/jobs/{state['job_id']}",
               lambda item: item["status"] in TERMINAL, interval=3,
               timeout=args.timeout, label="모델 연결")
    if job["status"] != "Succeeded":
        fail(f"모델 연결 {job['status']} · {job.get('error_code') or ''} {job.get('error') or ''}")
    return job["result"]["model_revision_id"]


def serve(client: httpx.Client, args: argparse.Namespace, state: dict, path: Path,
          model_id: str) -> None:
    """공급한 판본을 이 계정이 소유한 온라인 서빙 Endpoint에 배포하거나 전환한다.

    Endpoint 이름은 계정 안에서만 고유하고 관리자는 다른 계정의 Endpoint도 조회하므로,
    대상은 소유 Actor·이름으로 고른 뒤 상태 파일에 ID로 고정한다.
    서빙 Runtime이 새 판본의 준비를 확인한 뒤 활성 판본을 바꾸므로, 그때까지 기존 판본이 계속 처리한다.
    """
    pinned = state.get("endpoint")
    if pinned is not None and pinned.get("name") != args.endpoint:
        fail(f"이 상태 파일은 Endpoint {pinned.get('name')}에 공급합니다. 다른 Endpoint에는 새 상태 파일을 사용하세요.")
    if pinned is not None:
        existing = checked(client.get(f"/api/serving/endpoints/{pinned['id']}"))
        if existing.get("owner_actor") != state["actor_id"] or existing["name"] != args.endpoint:
            fail("상태 파일의 Endpoint가 이 계정의 같은 이름 Endpoint가 아닙니다.")
    else:
        existing = next((item for item in checked(client.get("/api/serving/endpoints"))
                         if item.get("owner_actor") == state["actor_id"]
                         and item["name"] == args.endpoint), None)
    if existing is None:
        endpoint = checked(client.post("/api/serving/endpoints",
                                       json={"name": args.endpoint, "model_revision_id": model_id}))
    elif existing["active_model_revision_id"] == model_id and existing["pending_model_revision_id"] is None:
        endpoint = existing
    else:
        endpoint = checked(client.put(f"/api/serving/endpoints/{existing['id']}/revision",
                                      json={"model_revision_id": model_id}))
    if pinned is None:
        state["endpoint"] = {"name": args.endpoint, "id": endpoint["id"]}
        save_state(path, state)

    def serving(item: dict) -> bool:
        pending = (item.get("runtime") or {}).get("pending") or {}
        if pending.get("status") == "DEPLOY_FAILED":
            fail(f"서빙 배포 실패 · {pending.get('status_reason') or '서빙 Runtime 로그를 확인하세요.'} "
                 "기존 판본은 계속 요청을 처리합니다.")
        return item["active_model_revision_id"] == model_id and item["ready"]

    wait(client, f"/api/serving/endpoints/{endpoint['id']}", serving, interval=2,
         timeout=args.timeout, label="온라인 서빙 준비")
    print(f"온라인 서빙 · Endpoint {args.endpoint} ({endpoint['id']}) · 활성 판본 {model_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="활동 이상 탐지 모델 공급(SOS 학습 또는 MLflow 버전 연결)·재개")
    parser.add_argument("--state-file", type=Path, required=True,
                        help="Dataset·학습 키·Job·Model ID를 보관할 공급 상태 JSON")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dataset-file", type=Path, help="새로 등록할 CSV·Parquet 파일")
    source.add_argument("--dataset-id", help="기존 Dataset ID 또는 등록 응답 유실 후 확인한 ID")
    source.add_argument("--mlflow-model", help="아직 SOS에 연결하지 않은 MLflow 모델 이름(Dagster connect_to_sos=false)")
    parser.add_argument("--mlflow-version", help="연결할 MLflow 모델 버전 번호(--mlflow-model과 함께)")
    description = parser.add_mutually_exclusive_group(required=True)
    description.add_argument("--description", help="목적·적용 대상·한계 설명")
    description.add_argument("--description-file", type=Path, help="설명 텍스트 파일")
    parser.add_argument("--api", default=os.getenv("SOS_API_URL", "http://127.0.0.1:9602"))
    parser.add_argument("--sample-rows", type=int, help="명시적으로 선택할 표본 행 수")
    parser.add_argument("--seed", type=int, help="표본 추출 Seed (새 공급 기본값: 42)")
    parser.add_argument("--timeout", type=float, default=3600, help="학습·연결·등록 대기 초")
    parser.add_argument("--endpoint", help="공급한 판본을 배포하거나 전환할 온라인 서빙 Endpoint 이름")
    args = parser.parse_args()
    args.state_file = args.state_file.resolve()
    if (args.mlflow_model is None) != (args.mlflow_version is None):
        parser.error("--mlflow-model과 --mlflow-version은 함께 지정하세요.")
    if args.mlflow_version is not None and not re.fullmatch(r"[1-9][0-9]{0,9}", args.mlflow_version):
        parser.error("--mlflow-version은 모델 버전 번호여야 합니다.")
    if args.mlflow_model is not None and (args.sample_rows is not None or args.seed is not None):
        parser.error("MLflow 버전 연결은 다시 학습하지 않으므로 --sample-rows·--seed를 쓰지 않습니다.")
    if args.sample_rows is not None and args.sample_rows < 2:
        parser.error("--sample-rows는 2 이상이어야 합니다.")
    if args.seed is not None and not 0 <= args.seed <= 2**32 - 1:
        parser.error("--seed 범위를 확인하세요.")
    if args.seed is not None and args.sample_rows is None and not args.state_file.exists():
        parser.error("새 공급의 --seed는 --sample-rows와 함께 지정하세요.")
    if args.timeout <= 0:
        parser.error("--timeout은 양수여야 합니다.")
    if args.endpoint is not None and not re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]", args.endpoint):
        parser.error("--endpoint는 영문 소문자·숫자·하이픈으로 된 2~63자 이름이어야 합니다.")
    token = os.getenv("SOS_SUPPLY_TOKEN")
    if not token:
        fail("SOS_SUPPLY_TOKEN 환경변수에 관리자 또는 automation 서비스 계정 토큰이 필요합니다.")
    try:
        text = (args.description if args.description is not None else
                args.description_file.read_text(encoding="utf-8")).strip()
        if not 1 <= len(text) <= 1000:
            fail("제공 설명은 1~1000자여야 합니다.")
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        # 원자적 상태 파일 교체와 별도로 같은 공급의 동시 실행을 막는다.
        with args.state_file.with_suffix(args.state_file.suffix + ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("같은 상태 파일을 사용하는 공급이 이미 실행 중입니다.")
            with httpx.Client(base_url=args.api, headers={"Authorization": f"Bearer {token}"},
                              timeout=httpx.Timeout(60, read=600)) as client:
                supply(client, args, text)
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        fail(f"공급을 완료하지 못했습니다({type(exc).__name__}). "
             "상태 파일을 보존하고 같은 명령으로 재개하세요.")


if __name__ == "__main__":
    main()
