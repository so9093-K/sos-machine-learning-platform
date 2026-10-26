"""Dagster 전환 이후 등록형 분석의 공개 계약과 상태 매핑 검증."""

from urllib.error import URLError

import pytest

from fastapi import HTTPException

from sos_platform import dagster_workflows, pipeline_reference
from sos_platform.api_pipelines import _candidate_rows
from sos_platform.dagster_client import DagsterClient, DagsterUnavailable


def test_registered_activity_method_keeps_independent_ml_contract():
    definition = pipeline_reference.definition()
    assert definition["id"] == "activity-anomaly-if"
    assert definition["learning_type"] == "unsupervised"
    assert definition["task_type"] == "anomaly_detection"
    assert len(definition["required_features"]) == 44
    assert definition["stages"] == list(dagster_workflows.STAGES)


class FakeStore:
    def get_model_revision(self, model_id):
        if model_id == "model-1":
            return {"id": model_id, "registry_version": "2"}
        return None


class FakeDagster:
    def __init__(self, status):
        self.run_status = status

    def find_by_submission(self, submission_id):
        assert submission_id == "submission-1"
        return None

    def status(self, run_id):
        assert run_id == "dagster-run-1"
        return self.run_status


def _row():
    return {
        "id": "submission-1",
        "dagster_run_id": "dagster-run-1",
        "definition_id": "activity-anomaly-if",
        "definition_version": "1",
        "dataset_id": "dataset-1",
        "dataset_version": "revision-1",
        "owner_actor": "owner-1",
        "created_at": "2026-10-26T00:00:00+00:00",
    }


def test_dagster_run_status_is_only_workflow_status(monkeypatch):
    monkeypatch.setattr(dagster_workflows, "_job_by_stage", lambda *_: None)
    for dagster_status, expected in [
        ("QUEUED", "Queued"), ("STARTED", "Running"),
        ("SUCCESS", "Succeeded"), ("FAILURE", "Failed"), ("CANCELED", "Cancelled"),
    ]:
        result = dagster_workflows._run(
            FakeStore(), _row(), client=FakeDagster(dagster_status)
        )
        assert result["status"] == expected
        assert result["job_ids"] == {}
        assert result["stages"] == []


def test_workflow_job_evidence_is_derived_not_replicated(monkeypatch):
    jobs = {
        "preflight": {"id": "preflight-1", "status": "Succeeded",
                      "kind": "ml_pipeline_preflight", "cancel_requested": False,
                      "result": {"plan": {"timestamp_column": "ts", "train_end": "a",
                                          "calibration_end": "b"}}},
        "training": {"id": "training-1", "status": "Succeeded", "kind": "ml_train",
                     "cancel_requested": False,
                     "result": {"model_revision_id": "model-1"}},
    }
    monkeypatch.setattr(
        dagster_workflows, "_job_by_stage",
        lambda store, row, stage: jobs.get(stage)
    )
    result = dagster_workflows._run(
        FakeStore(), _row(), client=FakeDagster("STARTED")
    )
    assert result["status"] == "Running"
    assert result["current_stage"] == "calibration"
    assert result["input_plan"]["timestamp_column"] == "ts"
    assert result["job_ids"] == {"preflight": "preflight-1", "training": "training-1"}


def test_dagster_graphql_outage_is_reported(monkeypatch):
    from sos_platform import dagster_client

    def unavailable(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr(dagster_client, "urlopen", unavailable)
    with pytest.raises(DagsterUnavailable):
        DagsterClient("http://example.test/graphql").status("run-1")


def test_cancelled_submission_without_dagster_run_is_not_queued(monkeypatch):
    monkeypatch.setattr(dagster_workflows, "_job_by_stage", lambda *_: None)
    row = {**_row(), "dagster_run_id": None, "cancel_requested": True}
    result = dagster_workflows._run(
        FakeStore(), row, client=FakeDagster("STARTED")
    )
    assert result["status"] == "Cancelled"
    assert result["cancel_requested"] is True
    assert result["current_stage"] is None


def test_candidate_lookup_never_assumes_missing_stage_evidence():
    with pytest.raises(HTTPException) as error:
        _candidate_rows(FakeStore(), {"stages": [], "owner_actor": "owner-1"}, 25, None)
    assert error.value.status_code == 409
