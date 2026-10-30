"""원본 Dataset 관측치 UUID와 Scoring·Explanation의 연결 계약을 검증한다."""

from contextlib import contextmanager
from uuid import UUID

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from sos_platform import ml_scoring
from sos_platform.ml_observation_identity import validate_observation_identity
from sos_platform.schemas import MLScoringRequest
from test_ml_scoring_temporal import scoring_env, scope, spec  # noqa: F401


def observation_uuid(index: int) -> str:
    """개인정보가 없는 반복 가능한 테스트 관측치 식별자."""
    return f"00000000-0000-4000-8000-{index:012x}"


def test_observation_uuid_preserved_across_batches(scoring_env, tmp_path):
    store, _, dataset, scorer, frames = scoring_env
    n = 50_021
    frames[0] = pl.DataFrame({
        "count": np.arange(n, dtype=np.float64),
        "observation_uuid": [observation_uuid(i) for i in range(n)],
        "user_name": ["private-name"] * n,
        "host_ip": ["192.0.2.10"] * n,
    })
    request = spec()
    request["observation_id_column"] = "observation_uuid"
    dest = tmp_path / "identity.parquet"
    summary = ml_scoring.run_dataset_scoring(store, dataset, request, dest)
    values = pl.read_parquet(dest)
    assert summary["row_count"] == n
    assert values["observation_id"][0] == observation_uuid(0)
    assert values["observation_id"][-1] == observation_uuid(n - 1)
    assert values["row_index"].to_list() == list(range(n))
    assert all(batch <= ml_scoring.SCORING_BATCH_ROWS for batch in scorer.batch_lengths)
    assert "user_name" not in values.columns
    assert "host_ip" not in values.columns
    assert "private-name" not in str(summary)
    assert "192.0.2.10" not in str(summary)
    assert summary["score_summary"]["observation_identity"] == {
        "kind": "opaque_uuid4",
        "source_column": "observation_uuid",
        "validated_rows": n,
        "unique_within": "selected_scoring_scope",
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
    }


def test_identity_is_unchanged_between_calibration_and_test(scoring_env, tmp_path):
    store, _, dataset, _, frames = scoring_env
    frames[0] = frames[0].with_columns(
        pl.Series("observation_uuid", [observation_uuid(i) for i in range(9)])
    )
    for partition, indices in (("calibration", (4, 5, 6)), ("test", (7, 8))):
        request = spec(partition)
        request["observation_id_column"] = "observation_uuid"
        path = tmp_path / f"{partition}.parquet"
        summary = ml_scoring.run_dataset_scoring(store, dataset, request, path)
        got = pl.read_parquet(path)
        assert got["observation_id"].to_list() == [observation_uuid(i) for i in indices]
        assert got["row_index"].to_list() == list(range(len(indices)))
        assert summary["score_summary"]["temporal_scope"]["partition"] == partition


@pytest.mark.parametrize("invalid", [
    ["not-an-id", observation_uuid(2)],
    [observation_uuid(1), observation_uuid(1)],
    [None, observation_uuid(2)],
    ["alice", "192.0.2.1"],
    ["AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", observation_uuid(2)],
])
def test_invalid_missing_duplicate_or_sensitive_observation_id_rejected(invalid):
    frame = pl.DataFrame({"obs": invalid}).lazy()
    with pytest.raises(ValueError, match="identity"):
        validate_observation_identity(frame, column="obs")


def test_identity_must_be_text_and_not_numeric_row_hash():
    frame = pl.DataFrame({"row_hash": [1.0, 2.0]}).lazy()
    with pytest.raises(ValueError, match="UUIDv4 String"):
        validate_observation_identity(frame, column="row_hash")


def test_selected_scope_duplicate_is_rejected_even_across_batches(scoring_env, tmp_path):
    store, _, dataset, _, frames = scoring_env
    count = ml_scoring.SCORING_BATCH_ROWS + 1
    values = [observation_uuid(i) for i in range(count)]
    values[-1] = values[0]
    frames[0] = pl.DataFrame({
        "count": np.arange(count, dtype=np.float64),
        "observation_uuid": values,
    })
    request = spec()
    request["observation_id_column"] = "observation_uuid"
    with pytest.raises(ValueError, match="duplicate"):
        ml_scoring.run_dataset_scoring(store, dataset, request, tmp_path / "dupes.parquet")
    assert not (tmp_path / "dupes.parquet").exists()


def test_legacy_scoring_keeps_previous_columns(scoring_env, tmp_path):
    store, _, dataset, _, _ = scoring_env
    summary = ml_scoring.run_dataset_scoring(store, dataset, spec(), tmp_path / "legacy.parquet")
    assert "observation_identity" not in summary["score_summary"]
    assert "observation_id" not in pl.read_parquet(tmp_path / "legacy.parquet").columns


def test_scoring_contract_rejects_identity_as_model_feature_or_label(scoring_env, tmp_path):
    store, model, dataset, _, frames = scoring_env
    model["features"] = ["count"]
    frames[0] = frames[0].with_columns(
        pl.Series("observation_uuid", [observation_uuid(i) for i in range(9)])
    )
    request = spec()
    request["observation_id_column"] = "count"
    with pytest.raises(ValueError, match="separate"):
        ml_scoring.run_dataset_scoring(store, dataset, request, tmp_path / "feature.parquet")
    request["observation_id_column"] = "observation_uuid"
    request["label_column"] = "observation_uuid"
    with pytest.raises(ValueError, match="separate"):
        ml_scoring.run_dataset_scoring(store, dataset, request, tmp_path / "label.parquet")


def test_request_schema_rejects_id_overlapping_time_or_label():
    with pytest.raises(ValidationError):
        MLScoringRequest(
            model_revision_id="model", dataset_id="data", label_column="uuid",
            observation_id_column="uuid",
        )
    with pytest.raises(ValidationError):
        MLScoringRequest(
            model_revision_id="model", dataset_id="data",
            temporal_scope=scope("test"), observation_id_column="ts",
        )
    request = MLScoringRequest(
        model_revision_id="model", dataset_id="data",
        observation_id_column="observation_uuid",
    )
    assert request.observation_id_column == "observation_uuid"


def test_structural_uuid_is_version_4():
    value = observation_uuid(111)
    assert UUID(value).version == 4


def test_http_scoring_identity_option_respects_dataset_and_model_ownership(
    monkeypatch, tmp_path
):
    """식별자 옵션으로 별도의 Dataset·Model 열람 권한을 우회할 수 없다."""
    from unittest.mock import Mock

    from fastapi.testclient import TestClient

    from sos_platform import api, api_ml_results
    from sos_platform.security import Actor, IdentitySettings

    dataset = {"id": "dataset-a", "version": "v1", "owner_actor": "alice"}
    model = {"id": "model-a", "owner_actor": "alice", "registry_version": "1",
             "features": ["f1"], "runtime": {"feature_dtypes": ["Float64"]}}
    fake_store = Mock()
    fake_store.get_dataset.return_value = dataset
    fake_store.get_model_revision.return_value = model
    # 다른 Actor에게 제공되지 않은 모델 판본이다.
    fake_store.get_model_offering.return_value = None

    def created(**kwargs):
        return {
            "id": "score-job", "kind": kwargs["kind"], "dataset_id": kwargs["dataset_id"],
            "spec": kwargs["spec"], "status": "Queued", "cancel_requested": False,
            "result": None, "error": None, "attempts": [],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }

    fake_store.create_job.side_effect = created
    monkeypatch.setattr(api, "Store", lambda home: fake_store)
    monkeypatch.setattr(api_ml_results, "discovery_columns", lambda store, dataset: {
        "columns": [{"name": "f1", "dtype": "Float64"}]})
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "http://localhost:5000")
    monkeypatch.setattr(api_ml_results, "verify_registered_version", lambda _client, _model: None)
    app = api.create_app(tmp_path, identity=IdentitySettings("development", {}))
    app.dependency_overrides[api_ml_results.CREATE_JOB_DEP.dependency] = (
        lambda: Actor("alice", "human", frozenset({"researcher"}))
    )
    request = {
        "dataset_id": dataset["id"],
        "model_revision_id": model["id"],
        "observation_id_column": "observation_uuid",
    }
    with TestClient(app) as client:
        response = client.post("/api/ml/scoring/jobs", json=request)
        assert response.status_code == 202, response.text
        fixed = fake_store.create_job.call_args.kwargs["spec"]
        assert fixed["observation_id_column"] == "observation_uuid"
        assert fixed["dataset_version"] == dataset["version"]

        dataset["owner_actor"] = "bob"
        denied_dataset = client.post("/api/ml/scoring/jobs", json=request)
        assert denied_dataset.status_code == 404
        dataset["owner_actor"] = "alice"

        model["owner_actor"] = "bob"
        denied_model = client.post("/api/ml/scoring/jobs", json=request)
        assert denied_model.status_code == 404
