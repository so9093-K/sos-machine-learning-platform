"""개발 환경의 활동 이상 탐지 모델을 SOS에 공급한다.

같은 --state-file로 재실행하면 등록 Dataset·학습 요청·멱등성 키를 재사용한다.
상태 파일에는 토큰을 저장하지 않는다. 새 학습은 별도의 상태 파일로 명시한다.
"""

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

DEFINITION_ID = "activity-anomaly-if"
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


def supply(client: httpx.Client, args: argparse.Namespace, description: str) -> None:
    """고정된 Dataset·학습 요청을 재개하고 실제 등록 판본만 제공한다."""
    identity = checked(client.get("/api/v2/auth/me"))
    if not {"model.train", "model.manage"}.issubset(identity["capabilities"]):
        fail("학습·모델 제공 권한이 있는 automation 서비스 계정이 필요합니다.")
    path = args.state_file
    api = str(client.base_url).rstrip("/")
    source = ({"file": str(args.dataset_file.resolve())} if args.dataset_file else
              {"id": args.dataset_id} if args.dataset_id else None)
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("version") != 1
                or state.get("api") != api or state.get("actor_id") != identity["actor_id"]):
            fail("상태 파일의 API·Actor·형식을 확인하세요. 다른 공급에는 새 상태 파일이 필요합니다.")
        # 업로드 응답이 유실된 경우 확인한 Dataset ID를 명시해 복구할 수 있다.
        recovering_upload = (args.dataset_id and state.get("upload_started") and
                             not state.get("dataset_id"))
        if recovering_upload:
            state["source"] = source
        elif source is not None and source != state["source"]:
            fail("상태 파일의 입력과 다릅니다. 새 입력에는 새 상태 파일을 사용하세요.")
        if args.sample_rows is not None and args.sample_rows != state["training_input"].get("rows"):
            fail("상태 파일의 표본 행 수와 다릅니다.")
        if args.seed is not None and args.seed != state["training_input"].get("seed"):
            fail("상태 파일의 Seed와 다릅니다.")
    else:
        if source is None:
            fail("새 공급에는 --dataset-file 또는 --dataset-id가 필요합니다.")
        state = {
            "version": 1, "api": api, "actor_id": identity["actor_id"], "source": source,
            "submission_key": str(uuid.uuid4()),
            "training_input": {"mode": "sample", "rows": args.sample_rows,
                               "seed": args.seed if args.seed is not None else 42}
                if args.sample_rows is not None else {"mode": "all"},
        }
        save_state(path, state)

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
        definition = next((item for item in checked(client.get("/api/pipelines"))
                           if item["id"] == DEFINITION_ID), None)
        if definition is None:
            fail("활동 이상 탐지 분석 정의를 찾을 수 없습니다.")
        state["training_request"] = {
            "dataset_id": state["dataset_id"], "features": definition["required_features"],
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
    job = wait(client, f"/api/ml/train/jobs/{state['job_id']}",
               lambda item: item["status"] in TERMINAL, interval=5,
               timeout=args.timeout, label="학습")
    if job["status"] != "Succeeded":
        fail(f"학습 {job['status']} · {job.get('error_code') or ''} {job.get('error') or ''}")
    model_id = job["result"]["model_revision_id"]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="활동 이상 탐지 모델 공급·재개")
    parser.add_argument("--state-file", type=Path, required=True,
                        help="Dataset·학습 키·Job·Model ID를 보관할 공급 상태 JSON")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dataset-file", type=Path, help="새로 등록할 CSV·Parquet 파일")
    source.add_argument("--dataset-id", help="기존 Dataset ID 또는 등록 응답 유실 후 확인한 ID")
    description = parser.add_mutually_exclusive_group(required=True)
    description.add_argument("--description", help="목적·적용 대상·한계 설명")
    description.add_argument("--description-file", type=Path, help="설명 텍스트 파일")
    parser.add_argument("--api", default=os.getenv("SOS_API_URL", "http://127.0.0.1:9602"))
    parser.add_argument("--sample-rows", type=int, help="명시적으로 선택할 표본 행 수")
    parser.add_argument("--seed", type=int, help="표본 추출 Seed (새 공급 기본값: 42)")
    parser.add_argument("--timeout", type=float, default=3600, help="학습·등록 대기 초")
    args = parser.parse_args()
    args.state_file = args.state_file.resolve()
    if args.sample_rows is not None and args.sample_rows < 2:
        parser.error("--sample-rows는 2 이상이어야 합니다.")
    if args.seed is not None and not 0 <= args.seed <= 2**32 - 1:
        parser.error("--seed 범위를 확인하세요.")
    if args.seed is not None and args.sample_rows is None and not args.state_file.exists():
        parser.error("새 공급의 --seed는 --sample-rows와 함께 지정하세요.")
    if args.timeout <= 0:
        parser.error("--timeout은 양수여야 합니다.")
    token = os.getenv("SOS_SUPPLY_TOKEN")
    if not token:
        fail("SOS_SUPPLY_TOKEN 환경변수에 automation 서비스 계정 토큰이 필요합니다.")
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
