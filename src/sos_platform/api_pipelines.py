"""신뢰된 등록형 Pipeline의 API-first 진입점."""
import base64
import binascii
import heapq
import json
import math

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal

from .cache import cached_dataset_file
from .pipeline_reference import PIPELINE_ID, definition, inspect_dataset
from .pipeline_runs import cancel_run, create_run, get_run, list_runs
from .security import Actor, require, resource_allowed
from .store import Store

READ_DATASET = Depends(require("dataset.read"))
READ_JOB = Depends(require("job.read"))
CREATE_JOB = Depends(require("job.create"))
CANCEL_JOB = Depends(require("job.cancel"))


class PipelineDefinitionOut(BaseModel):
    id: str
    version: str
    name: str
    description: str
    algorithm: str
    required_time_column: str
    required_features: list[str]
    stages: list[str]
    model_reuse: bool
    label_requirement: str
    limitations: list[str]


class PipelinePartitionSizesOut(BaseModel):
    train: int
    calibration: int
    test: int


class PipelinePlanOut(BaseModel):
    timestamp_column: str
    train_end: str
    calibration_end: str
    partition_sizes: PipelinePartitionSizesOut


class PipelineCheckOut(BaseModel):
    pipeline_id: str
    pipeline_version: str
    dataset_id: str
    dataset_version: str
    ready: bool
    issues: list[str]
    feature_count: int
    plan: PipelinePlanOut | None


class PipelineJobOut(BaseModel):
    id: str
    kind: str
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    error_code: str | None = None
    result: dict[str, object] | None


class PipelineStageOut(BaseModel):
    name: str
    job: PipelineJobOut


class PipelineRunOut(BaseModel):
    id: str
    definition_id: str
    definition_version: str
    dataset_id: str
    dataset_version: str
    input_plan: PipelinePlanOut | None
    stage_index: int
    job_ids: dict[str, str]
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    error_code: str | None = None
    created_at: str
    updated_at: str
    current_stage: str | None
    stages: list[PipelineStageOut] = Field(default_factory=list)


class PipelineCandidateOut(BaseModel):
    row_index: int
    anomaly_score: float
    features: dict[str, float | int | None]


class PipelineCandidatesOut(BaseModel):
    items: list[PipelineCandidateOut]
    threshold: float
    next_cursor: str | None
    result_id: str


class PipelineStartRequest(BaseModel):
    dataset_id: str = Field(min_length=1)


def _visible(actor: Actor, item: dict | None) -> dict:
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    resource_allowed(actor, item["owner_actor"])
    return item


def _cursor(value: str | None) -> tuple[float, int] | None:
    if value is None:
        return None
    if len(value) > 160:
        raise HTTPException(status_code=422, detail="Invalid cursor")
    try:
        item = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError
        score, index = item
        if type(score) not in (float, int) or not math.isfinite(score) or type(index) is not int or index < 0:
            raise ValueError
        return (float(score), index)
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        raise HTTPException(status_code=422, detail="Invalid cursor") from None


def _encode_cursor(rank: tuple[float, int]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(rank, separators=(",", ":")).encode("ascii")
    ).decode("ascii").rstrip("=")


def _candidate_rows(store: Store, run: dict, limit: int, cursor: str | None) -> dict:
    by_stage = {entry["name"]: entry["job"] for entry in run["stages"]}
    score_id = by_stage["test"]["result"]["scoring_result_id"]
    policy_id = by_stage["policy"]["result"]["policy_revision_id"]
    scoring = store.get_scoring_result(score_id)
    policy = store.get_decision_policy_revision(policy_id)
    if not scoring or not policy or scoring["owner_actor"] != run["owner_actor"] or policy["owner_actor"] != run["owner_actor"]:
        raise HTTPException(status_code=404, detail="Pipeline result unavailable")
    threshold = policy["threshold"]
    after = _cursor(cursor)
    # 이미 검증된 불변 S3 결과를 SOS의 기존 캐시·공유 읽기 Lease로 재사용한다.
    # 새 캐시 구현 없이 다운로드 중복과 조회 중 축출 경합을 방지한다.
    cache_entry = {
        "kind": "file", "version": scoring["sha256"],
        "location": scoring["location"],
    }
    with cached_dataset_file(store, cache_entry) as path:
        reader = pq.ParquetFile(path)

        def eligible():
            for batch in reader.iter_batches(
                batch_size=8192, columns=["row_index", "anomaly_score"]
            ):
                indices = batch.column(0).to_pylist()
                scores = batch.column(1).to_pylist()
                for index, score in zip(indices, scores, strict=True):
                    if not math.isfinite(score) or score < threshold:
                        continue
                    rank = (-float(score), int(index))
                    if after is None or rank > after:
                        yield rank

        # 원본 점수 파일은 불변이므로 동일 cursor로 결과 순서가 바뀌지 않는다.
        selected = heapq.nsmallest(limit + 1, eligible())
        has_more = len(selected) > limit
        page = selected[:limit]
        selected_ids = {idx for _, idx in page}
        feature_map: dict[int, dict] = {}
        if selected_ids:
            for batch in reader.iter_batches(
                batch_size=4096, columns=["row_index", "features"]
            ):
                indices = batch.column(0).to_pylist()
                values = batch.column(1)
                for position, idx in enumerate(indices):
                    if idx in selected_ids:
                        feature_map[int(idx)] = values[position].as_py()
                if len(feature_map) == len(selected_ids):
                    break
    return {
        "items": [
            {"row_index": idx, "anomaly_score": -rank,
             "features": feature_map.get(idx, {})}
            for rank, idx in page
        ],
        "threshold": threshold,
        "next_cursor": _encode_cursor(page[-1]) if has_more and page else None,
        "result_id": score_id,
    }


def pipeline_router(store: Store, *, identity_mode: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/pipelines", response_model=list[PipelineDefinitionOut])
    def pipelines(actor: Actor = READ_DATASET):
        return [definition()]

    @router.get("/api/pipelines/{definition_id}", response_model=PipelineDefinitionOut)
    def pipeline_detail(definition_id: str, actor: Actor = READ_DATASET):
        if definition_id != PIPELINE_ID:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return definition()

    @router.get("/api/pipelines/{definition_id}/check", response_model=PipelineCheckOut)
    def pipeline_check(
        definition_id: str,
        dataset_id: str = Query(min_length=1),
        actor: Actor = READ_DATASET,
    ):
        if definition_id != PIPELINE_ID:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        dataset = store.get_dataset(dataset_id)
        _visible(actor, dataset)
        return inspect_dataset(store, dataset)

    @router.post("/api/pipelines/{definition_id}/runs", status_code=202, response_model=PipelineRunOut)
    def submit_pipeline(
        definition_id: str,
        body: PipelineStartRequest,
        actor: Actor = CREATE_JOB,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key",
            min_length=8, max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        if definition_id != PIPELINE_ID:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        dataset = store.get_dataset(body.dataset_id)
        _visible(actor, dataset)
        if identity_mode == "token" and idempotency_key is None:
            raise HTTPException(status_code=400, detail="Idempotency-Key required")
        inspected = inspect_dataset(store, dataset)
        if not inspected["ready"]:
            raise HTTPException(status_code=422, detail=inspected["issues"])
        try:
            return create_run(store, dataset, actor.id, inspected, idempotency_key)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/pipeline-runs", response_model=list[PipelineRunOut])
    def pipeline_history(actor: Actor = READ_JOB):
        return list_runs(store, actor_id=None if actor.privileged else actor.id)

    @router.get("/api/pipeline-runs/{run_id}", response_model=PipelineRunOut)
    def pipeline_run_detail(run_id: str, actor: Actor = READ_JOB):
        return _visible(actor, get_run(store, run_id))

    @router.post("/api/pipeline-runs/{run_id}/cancel", response_model=PipelineRunOut)
    def pipeline_cancel(run_id: str, actor: Actor = CANCEL_JOB):
        _visible(actor, get_run(store, run_id, include_jobs=False))
        return cancel_run(store, run_id)

    @router.get("/api/pipeline-runs/{run_id}/candidates", response_model=PipelineCandidatesOut)
    def pipeline_candidates(
        run_id: str, limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=160),
        actor: Actor = READ_JOB,
    ):
        run = _visible(actor, get_run(store, run_id))
        if run["status"] != "Succeeded":
            raise HTTPException(status_code=409, detail="Pipeline run is not completed")
        return _candidate_rows(store, run, limit, cursor)

    return router
