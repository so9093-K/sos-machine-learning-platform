"""불변 Scoring Result의 점수순 관측치를 Pipeline과 독립적으로 조회한다."""
from collections.abc import Callable
import base64
import binascii
import heapq
import json
import math

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .cache import cached_dataset_file
from .security import Actor, require
from .store import Store


class ScoringObservationOut(BaseModel):
    row_index: int
    anomaly_score: float
    features: dict[str, float | int | None]
    observed_at: str | None = None
    observation_id: str | None = None


class ScoringObservationsOut(BaseModel):
    items: list[ScoringObservationOut]
    next_cursor: str | None
    result_id: str


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


def ranked_scoring_rows(
    store: Store, scoring: dict, limit: int, cursor: str | None,
    *, row_index: int | None = None, min_score: float | None = None,
) -> dict:
    """고정된 Scoring 파일을 점수 내림차순으로 탐색한다.

    전체 Scoring은 판정 기준 없이 순위만 보여주고, Pipeline은 확정된 정책
    임계값을 명시적으로 전달한다. 두 경우 모두 결과 원본을 수정하지 않는다.
    """
    if row_index is not None and cursor is not None:
        raise HTTPException(status_code=422, detail="Observation row lookup cannot use a page cursor")
    after = _cursor(cursor)
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
                    if not math.isfinite(score) or (min_score is not None and score < min_score):
                        continue
                    if row_index is not None and int(index) != row_index:
                        continue
                    rank = (-float(score), int(index))
                    if after is None or rank > after:
                        yield rank

        # 점수와 결과 행 번호로 정렬해 동점에서도 불변 cursor를 재현한다.
        selected = heapq.nsmallest(1 if row_index is not None else limit + 1, eligible())
        has_more = row_index is None and len(selected) > limit
        page = selected[:limit]
        selected_ids = {idx for _, idx in page}
        columns = ["row_index", "features"]
        for extra in ("observed_at", "observation_id"):
            if extra in reader.schema_arrow.names:
                columns.append(extra)
        detail_map: dict[int, dict] = {}
        if selected_ids:
            for batch in reader.iter_batches(batch_size=4096, columns=columns):
                indices = batch.column(0).to_pylist()
                for position, idx in enumerate(indices):
                    if idx in selected_ids:
                        detail_map[int(idx)] = {
                            "features": batch.column(1)[position].as_py(),
                            **{name: batch.column(offset)[position].as_py()
                               for offset, name in enumerate(columns[2:], start=2)},
                        }
                if len(detail_map) == len(selected_ids):
                    break
        if len(detail_map) != len(selected_ids):
            raise ValueError("Scoring artifact is missing selected observation rows")
    return {
        "items": [
            {"row_index": idx, "anomaly_score": -rank,
             "features": detail_map.get(idx, {}).get("features", {}),
             "observed_at": detail_map.get(idx, {}).get("observed_at"),
             "observation_id": detail_map.get(idx, {}).get("observation_id")}
            for rank, idx in page
        ],
        "next_cursor": _encode_cursor(page[-1]) if has_more and page else None,
        "result_id": scoring["id"],
    }


def scoring_observations_router(
    store: Store,
    check_ownership: Callable[[Actor, str | None, str, str], None],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/ml/scoring/results/{result_id}/observations", tags=["Scoring"], summary="점수화 결과 관측치 탐색",
        response_model=ScoringObservationsOut,
    )
    def list_scoring_observations(
        result_id: str,
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=160),
        row_index: int | None = Query(default=None, ge=0),
        actor: Actor = Depends(require("job.read")),
    ):
        scoring = store.get_scoring_result(result_id)
        if scoring is None:
            raise HTTPException(status_code=404, detail="Scoring Result not found")
        check_ownership(actor, scoring["owner_actor"], "scoring_result", result_id)
        return ranked_scoring_rows(store, scoring, limit, cursor, row_index=row_index)

    return router
