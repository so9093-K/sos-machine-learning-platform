"""불변 Scoring 결과의 관측값 색인으로 점수순 관측치를 페이지·행 번호로 조회한다."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import HTTPException

from sos_platform import scoring_index, scoring_observations
from sos_platform.duckdb_spill import SpillBudget


def _indexed(tmp_path, monkeypatch, name, rows, opened=None):
    """Worker와 같은 방식으로 색인 조각을 만들고, 조각 위치를 로컬 파일로 연결한 Store를 돌려준다."""
    source = tmp_path / f"{name}.parquet"
    pq.write_table(pa.Table.from_pylist(rows), source)
    stage = tmp_path / f"{name}-stage"
    stage.mkdir()
    scoring_index.build_observation_index(source, tmp_path / f"{name}.parts", SpillBudget(
        stage, 512, 2**30, "SOS-ML-SCORE-STAGE-LIMIT", "SOS-ML-SCORE-CAPACITY",
    ))
    parts = [{**{k: v for k, v in part.items() if k != "path"},
              "location": str(part["path"]), "sha256": "0" * 64}
             for part in scoring_index.part_files(tmp_path / f"{name}.parts")]

    @contextmanager
    def local(_store, entry):
        if opened is not None:
            opened.append(entry["location"])
        yield Path(entry["location"])

    monkeypatch.setattr(scoring_index, "cached_dataset_file", local)
    return SimpleNamespace(scoring_observation_parts=lambda _id: parts), {
        "id": name, "row_count": len(rows),
    }


def test_ranked_scoring_rows_page_through_the_index_in_score_order(tmp_path, monkeypatch):
    store, scoring = _indexed(tmp_path, monkeypatch, "result-1", [
        {"row_index": 0, "anomaly_score": 0.35, "features": {"count": 1.0}},
        {"row_index": 1, "anomaly_score": 0.92, "features": {"count": 2.0}},
        {"row_index": 2, "anomaly_score": 0.71, "features": {"count": 3.0}},
        {"row_index": 3, "anomaly_score": 0.71, "features": {"count": 4.0}},
    ])

    first = scoring_observations.ranked_scoring_rows(store, scoring, 2, None)
    assert first["result_id"] == scoring["id"]
    # 동점은 결과 행 번호 오름차순이다.
    assert [item["row_index"] for item in first["items"]] == [1, 2]
    assert first["next_cursor"]

    second = scoring_observations.ranked_scoring_rows(
        store, scoring, 2, first["next_cursor"],
    )
    assert [item["row_index"] for item in second["items"]] == [3, 0]
    assert [item["features"]["count"] for item in second["items"]] == [4.0, 1.0]
    assert second["next_cursor"] is None


def test_ranked_scoring_rows_wait_for_the_index_of_an_existing_result():
    job = {"status": "Queued", "error_code": None}
    store = SimpleNamespace(scoring_observation_parts=lambda _id: [],
                            scoring_index_job=lambda _id: job)
    with pytest.raises(HTTPException) as exc:
        scoring_observations.ranked_scoring_rows(store, {"id": "old", "row_count": 3}, 25, None)
    assert exc.value.status_code == 409 and "준비" in exc.value.detail
    job.update(status="Failed", error_code="SOS-ML-SCORE-STAGE-LIMIT")
    with pytest.raises(HTTPException) as exc:
        scoring_observations.ranked_scoring_rows(store, {"id": "old", "row_count": 3}, 25, None)
    assert "만들지 못했습니다 (SOS-ML-SCORE-STAGE-LIMIT)" in exc.value.detail



def test_ranked_scoring_rows_preserves_timestamp_and_features_for_scored_rows(tmp_path, monkeypatch):
    """점수순 정렬 이후에도 원본 관측 시각은 해당 Feature·행 번호에 붙어 있어야 한다."""
    store, scoring = _indexed(tmp_path, monkeypatch, "with-time", [
        {"row_index": 0, "anomaly_score": 0.22, "features": {"count": 1.0},
         "observed_at": "2026-01-01T00:00:00+00:00"},
        {"row_index": 1, "anomaly_score": 0.91, "features": {"count": 2.0},
         "observed_at": "2026-01-01T00:30:00+00:00"},
        {"row_index": 2, "anomaly_score": 0.66, "features": {"count": 3.0},
         "observed_at": "2026-01-01T01:00:00+00:00"},
    ])
    page = scoring_observations.ranked_scoring_rows(store, scoring, 2, None)
    assert [(item["row_index"], item["features"]["count"], item["observed_at"])
            for item in page["items"]] == [
        (1, 2.0, "2026-01-01T00:30:00+00:00"),
        (2, 3.0, "2026-01-01T01:00:00+00:00"),
    ]
    remainder = scoring_observations.ranked_scoring_rows(
        store, scoring, 2, page["next_cursor"])
    assert remainder["items"][0]["row_index"] == 0
    assert remainder["items"][0]["observed_at"] == "2026-01-01T00:00:00+00:00"



def test_ranked_scoring_row_lookup_and_cursor_conflict(tmp_path, monkeypatch):
    store, result = _indexed(tmp_path, monkeypatch, "result-2", [
        {"row_index": 0, "anomaly_score": 0.12, "features": {"value": 1.0}},
        {"row_index": 1, "anomaly_score": 0.89, "features": {"value": 2.0}},
    ])
    by_row = scoring_observations.ranked_scoring_rows(
        store, result, 25, None, row_index=0,
    )
    assert [item["row_index"] for item in by_row["items"]] == [0]
    assert by_row["items"][0]["features"]["value"] == 1.0
    assert scoring_observations.ranked_scoring_rows(
        store, result, 25, None, row_index=2,
    )["items"] == []

    with pytest.raises(HTTPException) as exc:
        scoring_observations.ranked_scoring_rows(store, result, 25, "cursor", row_index=0)
    assert exc.value.status_code == 422


def test_scoring_observation_api_respects_actor_and_ownership(tmp_path, monkeypatch):
    """job.read 권한과 Result 소유권이 모두 확인된 경우에만 행 값을 반환한다."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sos_platform.security import CURRENT_ACTOR_DEP, Actor, resource_allowed

    opened = []
    indexed, _ = _indexed(tmp_path, monkeypatch, "score-1", [
        {"row_index": 0, "anomaly_score": 0.8, "features": {"activity": 7.0}},
    ], opened)
    store = SimpleNamespace(
        scoring_observation_parts=indexed.scoring_observation_parts,
        get_scoring_result=lambda result_id: {
            "id": result_id, "owner_actor": "alice", "row_count": 1,
        } if result_id == "score-1" else None,
    )
    app = FastAPI()
    app.include_router(scoring_observations.scoring_observations_router(
        store, SimpleNamespace(check_ownership=lambda actor, owner, _kind, _id: resource_allowed(actor, owner)),
    ))
    actor = Actor("alice", "human", frozenset({"viewer"}))
    app.dependency_overrides[CURRENT_ACTOR_DEP.dependency] = lambda: actor

    with TestClient(app) as client:
        url = "/api/ml/scoring/results/score-1/observations"
        ok = client.get(url)
        assert ok.status_code == 200, ok.text
        assert ok.json()["items"][0]["features"]["activity"] == 7.0
        assert len(opened) == 1

        # 읽기 권한이 있는 다른 Actor라도 원본 점수 파일에 접근하면 안 된다.
        actor = Actor("bob", "human", frozenset({"viewer"}))
        hidden = client.get(url)
        assert hidden.status_code == 404
        assert len(opened) == 1

        # 관리 권한과 읽기 권한은 별개다.
        actor = Actor("operator", "human", frozenset({"operator"}))
        forbidden = client.get(url)
        assert forbidden.status_code == 403
        assert len(opened) == 1

        missing = client.get("/api/ml/scoring/results/not-found/observations")
        assert missing.status_code == 403
