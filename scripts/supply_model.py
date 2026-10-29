"""개발 환경에서 준비한 활동 이상 탐지 모델을 SOS에 공급한다.

SOS Worker로 학습해 판본·Runtime·Registry 근거를 남기고, 확인된 판본을 설명과 함께
사용자에게 제공한다. `automation` 역할의 서비스 계정 토큰을 SOS_SUPPLY_TOKEN으로 전달한다.

  SOS_SUPPLY_TOKEN=... python scripts/supply_model.py \\
      --dataset-file data/ml_features.parquet --description-file docs/model.txt
"""

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

DEFINITION_ID = "activity-anomaly-if"
TERMINAL = {"Succeeded", "Failed", "Cancelled"}
# 등록 Workflow의 활동 이상 탐지 학습과 같은 설정을 사용한다.
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


def checked(response: httpx.Response) -> dict:
    if response.is_error:
        try:
            body = response.json()
            detail = body.get("detail")
            code = body.get("code")
        except ValueError:
            detail, code = response.text[:300], None
        fail(f"HTTP {response.status_code} {code or ''} {detail}")
    return response.json()


def wait(
    client: httpx.Client, path: str, done, *, interval: float, timeout: float, label: str
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        item = checked(client.get(path))
        if done(item):
            return item
        if time.monotonic() > deadline:
            fail(f"{label} 대기 시간이 초과됐습니다.")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="활동 이상 탐지 모델을 학습해 SOS 사용자에게 제공한다."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset-file", type=Path, help="등록할 학습 데이터 파일(CSV·Parquet)")
    source.add_argument("--dataset-id", help="이미 등록한 학습 Dataset ID")
    description = parser.add_mutually_exclusive_group(required=True)
    description.add_argument("--description", help="사용자가 읽을 목적·적용 대상·한계 설명")
    description.add_argument("--description-file", type=Path, help="설명 텍스트 파일")
    parser.add_argument("--api", default=os.getenv("SOS_API_URL", "http://127.0.0.1:9602"))
    parser.add_argument("--sample-rows", type=int, help="학습에 사용할 무작위 표본 행 수")
    parser.add_argument("--seed", type=int, default=42, help="표본 추출 Seed")
    parser.add_argument("--timeout", type=float, default=3600, help="학습·등록 대기 초")
    args = parser.parse_args()

    token = os.getenv("SOS_SUPPLY_TOKEN")
    if not token:
        fail("SOS_SUPPLY_TOKEN 환경변수에 automation 역할의 서비스 계정 토큰이 필요합니다.")
    text = args.description if args.description is not None else args.description_file.read_text()
    if not text.strip():
        fail("제공 설명이 비어 있습니다.")

    with httpx.Client(
        base_url=args.api,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(60, read=600),
    ) as client:
        identity = checked(client.get("/api/v2/auth/me"))
        if (
            "model.train" not in identity["capabilities"]
            or "model.manage" not in identity["capabilities"]
        ):
            fail(
                "토큰에 학습·모델 제공 권한이 없습니다. automation 역할의 서비스 계정을 사용하세요."
            )

        definition = next(
            (item for item in checked(client.get("/api/pipelines")) if item["id"] == DEFINITION_ID),
            None,
        )
        if definition is None:
            fail("활동 이상 탐지 분석 정의를 찾을 수 없습니다.")

        if args.dataset_file:
            with args.dataset_file.open("rb") as handle:
                dataset = checked(
                    client.post(
                        "/api/datasets/files", files={"file": (args.dataset_file.name, handle)}
                    )
                )
            print(f"학습 Dataset 등록 · {dataset['name']} · {dataset['id']}")
        else:
            dataset = checked(client.get(f"/api/datasets/{args.dataset_id}"))

        training_input = (
            {"mode": "sample", "rows": args.sample_rows, "seed": args.seed}
            if args.sample_rows
            else {"mode": "all"}
        )
        job = checked(
            client.post(
                "/api/ml/train/jobs",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                json={
                    "dataset_id": dataset["id"],
                    "features": definition["required_features"],
                    "params": PARAMS,
                    "training_input": training_input,
                    "missing_policy": "train_median",
                    "experiment_title": "제공 모델",
                },
            )
        )
        print(f"학습 Job 접수 · {job['id']}")
        job = wait(
            client,
            f"/api/ml/train/jobs/{job['id']}",
            lambda item: item["status"] in TERMINAL,
            interval=5,
            timeout=args.timeout,
            label="학습",
        )
        if job["status"] != "Succeeded":
            fail(f"학습 {job['status']} · {job.get('error_code') or ''} {job.get('error') or ''}")
        model_id = job["result"]["model_revision_id"]
        model = wait(
            client,
            f"/api/ml/models/{model_id}",
            lambda item: bool(item.get("registry_version")),
            interval=3,
            timeout=args.timeout,
            label="Registry 등록",
        )
        print(f"모델 판본 확인 · {model_id} · Registry v{model['registry_version']}")

        offering = checked(
            client.put(f"/api/ml/models/{model_id}/offering", json={"description": text})
        )
        if not offering["offered"]:
            fail("제공 상태를 확인하지 못했습니다.")
        print(f"제공 완료 · {model_id}")


if __name__ == "__main__":
    main()
