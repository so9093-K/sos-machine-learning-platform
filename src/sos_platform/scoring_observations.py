"""불변 Scoring Result의 점수순 관측치를 관측값 색인으로 조회한다."""
import base64
import binascii
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .api_access import ResourceAccess
from .scoring_index import rank_of_row, ranked_rows
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


_CURSOR_ERROR = "페이지 위치가 올바르지 않습니다. 목록을 새로 조회하세요."


def _cursor(value: str | None) -> int:
    """다음 페이지가 시작하는 점수 순위(0부터)."""
    if value is None:
        return 0
    if len(value) > 160:
        raise HTTPException(status_code=422, detail=_CURSOR_ERROR)
    try:
        item = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(item, dict) or set(item) != {"rank"} or type(item["rank"]) is not int \
                or item["rank"] < 0:
            raise ValueError
        return item["rank"]
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        raise HTTPException(status_code=422, detail=_CURSOR_ERROR) from None


def _encode_cursor(rank: int) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"rank": rank}, separators=(",", ":")).encode("ascii")
    ).decode("ascii").rstrip("=")


def ranked_scoring_rows(
    store: Store, scoring: dict, limit: int, cursor: str | None, *, row_index: int | None = None,
) -> dict:
    """고정된 Scoring 결과를 점수 내림차순(동점은 결과 행 번호 오름차순)으로 탐색한다.

    결과마다 한 번 만든 관측값 색인 조각에서 필요한 부분만 읽는다. 페이지마다 전체를 다시 정렬하거나
    결과 파일 전체를 내려받지 않으며, 결과 원본을 수정하지 않는다.
    """
    if row_index is not None and cursor is not None:
        raise HTTPException(status_code=422, detail="개별 관측값 조회에는 페이지 커서를 사용할 수 없습니다.")
    parts = store.scoring_observation_parts(scoring["id"])
    if not parts:
        # Scoring은 결과를 게시할 때 색인을 함께 게시한다. 색인이 없으면 게시 불변식이 깨진 것이다.
        raise ValueError("Scoring result has no observation index")
    if row_index is not None:
        rank = rank_of_row(store, parts, row_index)
        rows = ranked_rows(store, parts, rank, 1) if rank is not None else []
        has_more = False
    else:
        start = _cursor(cursor)
        rows = ranked_rows(store, parts, start, limit)
        has_more = start + len(rows) < scoring["row_count"]
    return {
        "items": [
            {"row_index": row["row_index"], "anomaly_score": row["anomaly_score"],
             "features": row.get("features") or {},
             "observed_at": row.get("observed_at"),
             "observation_id": row.get("observation_id")}
            for row in rows
        ],
        "next_cursor": _encode_cursor(start + len(rows)) if has_more and rows else None,
        "result_id": scoring["id"],
    }


READ_JOB = Depends(require("job.read"))


def scoring_observations_router(store: Store, access: ResourceAccess) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/ml/scoring/results/{result_id}/observations",
        tags=["Scoring"],
        summary="점수화 결과 관측치 탐색",
        description=(
            "보존된 점수와 관측 근거를 점수 내림차순(동점은 결과 행 번호 오름차순)으로 조회합니다. 결과마다 "
            "한 번 만든 관측값 색인을 읽으므로 결과 크기와 관계없이 페이지 비용이 같습니다. 결과 행 번호는 "
            "원본 이벤트의 고유 식별자가 아닙니다."
        ),
        response_model=ScoringObservationsOut,
    )
    def list_scoring_observations(
        result_id: str,
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=160),
        row_index: int | None = Query(default=None, ge=0),
        actor: Actor = READ_JOB,
    ):
        scoring = store.get_scoring_result(result_id)
        if scoring is None:
            raise HTTPException(status_code=404, detail="점수 결과를 찾을 수 없습니다.")
        access.check_ownership(actor, scoring["owner_actor"], "scoring_result", result_id)
        return ranked_scoring_rows(store, scoring, limit, cursor, row_index=row_index)

    return router
