"""공급 중 응답 유실·재실행이 Dataset 등록과 학습을 중복시키지 않는지 확인한다."""

import argparse
import json
import runpy
from pathlib import Path

import httpx
import pytest

supply_model = runpy.run_path(str(Path(__file__).parents[1] / "scripts/supply_model.py"))


@pytest.mark.parametrize("lost_at", ["training", "registry", "offering"])
def test_supply_resumes_the_same_dataset_and_training_request(tmp_path, lost_at):
    args = argparse.Namespace(state_file=tmp_path / "supply.json", dataset_id=None,
        dataset_file=tmp_path / "input.parquet", sample_rows=None, seed=None, timeout=1,
        endpoint=None)
    args.dataset_file.write_bytes(b"controlled-upload")
    counts = {"upload": 0, "training": 0, "offer": 0}
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
            supply_model["supply"](client, args, "목적·입력·한계")
        supply_model["supply"](client, args, "목적·입력·한계")
        supply_model["supply"](client, args, "목적·입력·한계")
    assert counts["upload"] == counts["offer"] == 1
    assert len(submissions) == 1
    assert counts["training"] == (2 if lost_at == "training" else 1)
    state = json.loads(args.state_file.read_text())
    assert (state["dataset_id"], state["job_id"], state["model_id"]) == (
        "dataset-1", "job-1", "model-1")


def test_uncertain_upload_is_not_repeated_and_can_resume_with_dataset_id(tmp_path, capsys):
    args = argparse.Namespace(state_file=tmp_path / "supply.json", dataset_id=None,
        dataset_file=tmp_path / "input.parquet", sample_rows=None, seed=None, timeout=1,
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
