"""불변 Scoring 점수순 관측치 조회의 실제 페이지 및 Pipeline 임계값 동작."""
from contextlib import contextmanager

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import HTTPException

from sos_platform import scoring_observations


def test_ranked_scoring_rows_reuses_immutable_artifact_without_a_policy(tmp_path, monkeypatch):
    path = tmp_path / "scores.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"row_index": 0, "anomaly_score": 0.35, "features": {"count": 1.0}},
        {"row_index": 1, "anomaly_score": 0.92, "features": {"count": 2.0}},
        {"row_index": 2, "anomaly_score": 0.71, "features": {"count": 3.0}},
        {"row_index": 3, "anomaly_score": 0.71, "features": {"count": 4.0}},
    ]), path)

    @contextmanager
    def local_cache(*_args):
        yield path

    monkeypatch.setattr(scoring_observations, "cached_dataset_file", local_cache)
    scoring = {"id": "result-1", "sha256": "abc", "location": "ignored"}

    first = scoring_observations.ranked_scoring_rows(None, scoring, 2, None)
    assert first["result_id"] == scoring["id"]
    assert [item["row_index"] for item in first["items"]] == [1, 2]
    assert first["next_cursor"]

    second = scoring_observations.ranked_scoring_rows(
        None, scoring, 2, first["next_cursor"],
    )
    assert [item["row_index"] for item in second["items"]] == [3, 0]
    assert second["next_cursor"] is None

    # Pipeline 판정 후보는 같은 점수 정렬에서 정책 임계값만 적용한다.
    only_alerts = scoring_observations.ranked_scoring_rows(
        None, scoring, 10, None, min_score=0.71,
    )
    assert [item["row_index"] for item in only_alerts["items"]] == [1, 2, 3]


def test_ranked_scoring_row_lookup_and_cursor_conflict(tmp_path, monkeypatch):
    path = tmp_path / "scores.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"row_index": 0, "anomaly_score": 0.12, "features": {"value": 1.0}},
        {"row_index": 1, "anomaly_score": 0.89, "features": {"value": 2.0}},
    ]), path)

    @contextmanager
    def local_cache(*_args):
        yield path

    monkeypatch.setattr(scoring_observations, "cached_dataset_file", local_cache)
    result = {"id": "result-2", "sha256": "def", "location": "ignored"}
    by_row = scoring_observations.ranked_scoring_rows(
        None, result, 25, None, row_index=0,
    )
    assert [item["row_index"] for item in by_row["items"]] == [0]
    assert by_row["items"][0]["features"]["value"] == 1.0

    with pytest.raises(HTTPException) as exc:
        scoring_observations.ranked_scoring_rows(None, result, 25, "cursor", row_index=0)
    assert exc.value.status_code == 422


def test_scoring_observation_api_respects_actor_and_ownership(tmp_path, monkeypatch):
    """job.read 권한과 Result 소유권이 모두 확인된 경우에만 행 값을 반환한다."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sos_platform.security import Actor, CURRENT_ACTOR_DEP, resource_allowed

    parquet = tmp_path / "visible.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"row_index": 0, "anomaly_score": 0.8, "features": {"activity": 7.0}},
    ]), parquet)

    opened = []

    @contextmanager
    def cache(*_args):
        opened.append(True)
        yield parquet

    monkeypatch.setattr(scoring_observations, "cached_dataset_file", cache)
    store = SimpleNamespace(get_scoring_result=lambda result_id: {
        "id": result_id, "owner_actor": "alice", "sha256": "stored", "location": "ignored",
    } if result_id == "score-1" else None)
    app = FastAPI()
    app.include_router(scoring_observations.scoring_observations_router(
        store, lambda actor, owner, _kind, _id: resource_allowed(actor, owner),
    ))
    actor = Actor("alice", "human", frozenset({"viewer"}))
    app.dependency_overrides[CURRENT_ACTOR_DEP.dependency] = lambda: actor

    with TestClient(app) as client:
        url = "/api/ml/scoring/results/score-1/observations"
        ok = client.get(url)
        assert ok.status_code == 200, ok.text
        assert ok.json()["items"][0]["features"]["activity"] == 7.0
        assert opened == [True]

        # 읽기 권한이 있는 다른 Actor라도 원본 점수 파일에 접근하면 안 된다.
        actor = Actor("bob", "human", frozenset({"viewer"}))
        hidden = client.get(url)
        assert hidden.status_code == 404
        assert opened == [True]

        # 관리 권한과 읽기 권한은 별개다.
        actor = Actor("operator", "human", frozenset({"operator"}))
        forbidden = client.get(url)
        assert forbidden.status_code == 403
        assert opened == [True]

        missing = client.get("/api/ml/scoring/results/not-found/observations")
        assert missing.status_code == 403
