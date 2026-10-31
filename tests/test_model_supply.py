"""공급 중 응답 유실·재실행이 Dataset 등록과 학습을 중복시키지 않는지 확인한다."""

import argparse
import json
import runpy
from pathlib import Path

import httpx
import pytest

supply_model = runpy.run_path(str(Path(__file__).parents[1] / "scripts/supply_model.py"))


@pytest.mark.parametrize("lost_at", ["example", "training", "registry", "offering"])
def test_supply_resumes_the_same_dataset_and_training_request(tmp_path, lost_at):
    args = argparse.Namespace(state_file=tmp_path / "supply.json", dataset_id=None,
        dataset_file=tmp_path / "input.parquet", example=None, sample_rows=None, seed=None, timeout=1,
        endpoint=None)
    args.dataset_file.write_bytes(b"controlled-upload")
    if lost_at == "example":
        args.dataset_file, args.example = None, "activity-anomaly-if"
    description = None if args.example else "목적·입력·한계"
    counts = {"upload": 0, "example": 0, "training": 0, "offer": 0}
    submissions = {}
    outage = [True]
    offered = [False]

    def handler(request):
        path = request.url.path
        if path == "/api/v2/auth/me":
            body = {"actor_id": "supplier", "capabilities": ["model.train", "model.manage"]}
        elif path == "/api/datasets/files":
            counts["upload"] += 1
            body = {"id": "dataset-1"}
        elif path == "/api/examples/activity-anomaly-if/use":
            counts["example"] += 1
            if outage[0]:
                outage[0] = False
                raise httpx.ReadError("example registered but response lost", request=request)
            # 예제 API는 동일 Actor·내용 판본을 재사용한다.
            body = {"id": "dataset-1"}
        elif path == "/api/pipelines":
            body = [{"id": "activity-anomaly-if", "required_features": ["a"]}]
        elif path == "/api/ml/train/jobs":
            counts["training"] += 1
            key = request.headers["Idempotency-Key"]
            payload = json.loads(request.content)
            if key in submissions:
                assert submissions[key] == payload
            submissions[key] = payload
            body = {"id": "job-1"}
            if lost_at == "training" and outage[0]:
                outage[0] = False
                raise httpx.ReadError("accepted but response lost", request=request)
        elif path == "/api/ml/train/jobs/job-1":
            body = {"status": "Succeeded", "result": {"model_revision_id": "model-1"}}
        elif path == "/api/ml/models/model-1":
            if lost_at == "registry" and outage[0]:
                outage[0] = False
                raise httpx.ReadError("registry disconnected", request=request)
            body = {"lifecycle_state": "Active", "registry_version": "1"}
        elif path == "/api/ml/models/model-1/offering":
            if request.method == "PUT":
                counts["offer"] += 1
                offered[0] = True
                if lost_at == "offering" and outage[0]:
                    outage[0] = False
                    raise httpx.ReadError("offered but response lost", request=request)
            body = {"offered": offered[0], "description": "목적·입력·한계" if offered[0] else None}
        else:
            pytest.fail(f"Unexpected request: {request.method} {path}")
        return httpx.Response(200, json=body)

    with httpx.Client(base_url="http://sos.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            supply_model["supply"](client, args, description)
        supply_model["supply"](client, args, description)
        supply_model["supply"](client, args, description)
    assert counts["offer"] == (0 if lost_at == "example" else 1)
    assert counts["upload"] == (0 if lost_at == "example" else 1)
    assert counts["example"] == (2 if lost_at == "example" else 0)
    assert len(submissions) == 1
    assert counts["training"] == (2 if lost_at == "training" else 1)
    state = json.loads(args.state_file.read_text())
    assert (state["dataset_id"], state["job_id"], state["model_id"]) == (
        "dataset-1", "job-1", "model-1")


def test_uncertain_upload_is_not_repeated_and_can_resume_with_dataset_id(tmp_path, capsys):
    args = argparse.Namespace(state_file=tmp_path / "supply.json", dataset_id=None,
        dataset_file=tmp_path / "input.parquet", example=None, sample_rows=None, seed=None, timeout=1,
        endpoint=None)
    args.dataset_file.write_bytes(b"controlled-upload")
    uploads = []

    def handler(request):
        if request.url.path == "/api/v2/auth/me":
            return httpx.Response(200, json={"actor_id": "supplier",
                "capabilities": ["model.train", "model.manage"]})
        if request.url.path == "/api/datasets/files":
            uploads.append(request)
            raise httpx.ReadError("response lost", request=request)
        if request.url.path == "/api/datasets/recovered":
            return httpx.Response(200, json={"id": "recovered"})
        # 여기서 중단해도 확인한 Dataset ID가 먼저 보존돼야 한다.
        raise httpx.ReadError("not needed for this check", request=request)

    with httpx.Client(base_url="http://sos.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            supply_model["supply"](client, args, "설명")
        with pytest.raises(SystemExit):
            supply_model["supply"](client, args, "설명")
        assert "파일은 다시 전송하지 않습니다" in capsys.readouterr().err
        assert len(uploads) == 1
        args.dataset_file, args.dataset_id = None, "recovered"
        with pytest.raises(httpx.ReadError):
            supply_model["supply"](client, args, "설명")
    assert json.loads(args.state_file.read_text())["dataset_id"] == "recovered"


@pytest.mark.parametrize("offered", [True, False])
def test_existing_example_requires_explicit_withdrawal_and_is_not_reoffered(tmp_path, capsys, offered):
    args = argparse.Namespace(state_file=tmp_path / "supply.json", dataset_id=None,
        dataset_file=None, example=None, sample_rows=None, seed=None, timeout=1, endpoint=None)
    args.state_file.write_text(json.dumps({
        "version": 1, "api": "http://sos.test", "actor_id": "supplier",
        "source": {"example": "activity-anomaly-if"}, "dataset_id": "dataset-1",
        "training_input": {"mode": "all"}, "training_request": {"dataset_id": "dataset-1"}, "job_id": "job-1",
    }))

    def handler(request):
        assert request.method == "GET", "기존 합성 모델 재개는 등록·학습·제공을 변경하지 않는다."
        if request.url.path == "/api/v2/auth/me":
            body = {"actor_id": "supplier", "capabilities": ["model.train", "model.manage"]}
        elif request.url.path == "/api/ml/train/jobs/job-1":
            body = {"status": "Succeeded", "result": {"model_revision_id": "model-1"}}
        elif request.url.path == "/api/ml/models/model-1":
            body = {"lifecycle_state": "Active", "registry_version": "1"}
        elif request.url.path == "/api/ml/models/model-1/offering":
            body = {"offered": offered, "withdrawn_at": None if offered else "2026-10-31T00:00:00Z"}
        else:
            pytest.fail(f"Unexpected request: {request.method} {request.url.path}")
        return httpx.Response(200, json=body)

    with httpx.Client(base_url="http://sos.test", transport=httpx.MockTransport(handler)) as client:
        if offered:
            with pytest.raises(SystemExit):
                supply_model["supply"](client, args, None)
            assert "명시적으로 중단한 뒤 재개" in capsys.readouterr().err
        else:
            supply_model["supply"](client, args, None)
            assert "제공 요청 없음" in capsys.readouterr().out
