"""Dagster 전환 이후 등록형 분석의 공개 계약과 상태 매핑 검증."""

from urllib.error import URLError

import pytest
from fastapi import HTTPException

from sos_platform import dagster_workflows, pipeline_reference
from sos_platform.api_pipelines import PipelineRunOut, _candidate_rows
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


def test_unknown_dagster_run_status_is_reported_as_dependency_error(monkeypatch):
    monkeypatch.setattr(dagster_workflows, "_job_by_stage", lambda *_: None)
    with pytest.raises(DagsterUnavailable) as failed:
        dagster_workflows._run(FakeStore(), _row(), client=FakeDagster("UNKNOWN"))
    assert failed.value.operation == "run_status"
    assert failed.value.failure_type == "unexpected_result"


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
        assert result["stages"] == []
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
    assert [s["name"] for s in result["stages"]] == ["preflight", "training"]


def test_failed_stage_preserves_public_error_and_retry_policy(monkeypatch):
    job = {
        "id": "training-1", "status": "Failed", "kind": "ml_train",
        "cancel_requested": False, "result": None,
        "error_code": "SOS-ML-INPUT-INVALID",
        "error": "숫자형 Feature·결측값·값의 범위·학습 파라미터를 확인한 뒤 다시 제출하세요.",
        "retry_policy": "never",
    }
    monkeypatch.setattr(dagster_workflows, "_job_by_stage",
                        lambda store, row, stage: job if stage == "training" else None)
    response = PipelineRunOut.model_validate(dagster_workflows._run(
        FakeStore(), _row(), client=FakeDagster("FAILURE")
    )).model_dump()
    failed = response["stages"][0]["job"]
    assert failed["error"] == job["error"]
    assert failed["retry_policy"] == "never"
    assert response["current_stage"] is None


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
    # 취소 확정 후에는 더 이상 "취소 요청 중"으로 표시하지 않는다.
    assert result["cancel_requested"] is False
    assert result["current_stage"] is None


def test_candidate_lookup_never_assumes_missing_stage_evidence():
    with pytest.raises(HTTPException) as error:
        _candidate_rows(FakeStore(), {"stages": [], "owner_actor": "owner-1"}, 25, None)
    assert error.value.status_code == 409


def test_registry_not_configured_retains_domain_error(monkeypatch):
    class UnregisteredStore(FakeStore):
        def get_model_revision(self, model_id):
            return {"id": model_id, "registry_version": None}

        def tracking_status(self, job_id):
            assert job_id == "train-1"
            return None

    jobs = {
        "training": {
            "id": "train-1", "status": "Succeeded", "kind": "ml_train",
            "cancel_requested": False, "result": {"model_revision_id": "model-1"},
        },
    }
    monkeypatch.setattr(
        dagster_workflows, "_job_by_stage", lambda store, row, stage: jobs.get(stage)
    )
    result = dagster_workflows._run(
        UnregisteredStore(), _row(), client=FakeDagster("FAILURE")
    )
    assert result["status"] == "Failed"
    assert result["error_code"] == "SOS-MLFLOW-NOT-CONFIGURED"


def test_dagster_submission_lookup_uses_supported_tag_filter(monkeypatch):
    """실제 발생한 RunsFilter.jobName HTTP 400 회귀를 직접 잡는다."""
    import io
    import json

    from sos_platform import dagster_client

    def query(req, timeout):
        payload = json.loads(req.data)
        source = payload["query"]
        assert "filter: {tags:" in source
        assert "filter: {jobName:" not in source
        assert "results {runId jobName}" in source
        assert payload["variables"]["id"] == "submission-1"
        return io.BytesIO(json.dumps({
            "data": {"runsOrError": {"__typename": "Runs", "results": [
                {"runId": "dagster-run-1", "jobName": "activity_anomaly_workflow"}
            ]}}
        }).encode())

    monkeypatch.setattr(dagster_client, "urlopen", query)
    assert DagsterClient("http://example.test/graphql").find_by_submission(
        "submission-1"
    ) == "dagster-run-1"


def test_dagster_submission_lookup_rejects_wrong_job_and_duplicates(monkeypatch):

    client = DagsterClient("http://example.test/graphql")
    monkeypatch.setattr(client, "_execute", lambda *args, **kwargs: {
        "runsOrError": {"__typename": "Runs", "results": [
            {"runId": "other-run", "jobName": "unrelated_job"}
        ]}
    })
    with pytest.raises(DagsterUnavailable) as mismatch:
        client.find_by_submission("submission-1")
    assert mismatch.value.failure_type == "job_mismatch"
    monkeypatch.setattr(client, "_execute", lambda *args, **kwargs: {
        "runsOrError": {"__typename": "Runs", "results": [
            {"runId": "one", "jobName": "activity_anomaly_workflow"},
            {"runId": "two", "jobName": "activity_anomaly_workflow"},
        ]}
    })
    with pytest.raises(DagsterUnavailable) as duplicate:
        client.find_by_submission("submission-1")
    assert duplicate.value.failure_type == "duplicate_submission"


def test_dagster_graphql_http_400_distinguished_from_connection_outage(monkeypatch):
    from urllib.error import HTTPError

    from sos_platform import dagster_client

    def rejected(*args, **kwargs):
        raise HTTPError("http://example.test/graphql", 400, "Bad Request", {}, None)

    monkeypatch.setattr(dagster_client, "urlopen", rejected)
    with pytest.raises(DagsterUnavailable) as failed:
        DagsterClient("http://example.test/graphql").find_by_submission("submission-1")
    assert failed.value.operation == "run_lookup"
    assert failed.value.failure_type == "http_error"
    assert failed.value.http_status == 400


def test_dagster_graphql_errors_are_distinguished(monkeypatch):
    import io

    from sos_platform import dagster_client

    monkeypatch.setattr(dagster_client, "urlopen", lambda *args, **kwargs:
                        io.BytesIO(b'{"errors":[{"message":"unknown field"}],"data":null}'))
    with pytest.raises(DagsterUnavailable) as failed:
        DagsterClient("http://example.test/graphql").find_by_submission("submission-1")
    assert failed.value.failure_type == "graphql_error"


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("find_by_submission", {"runsOrError": {"__typename": "Runs", "results": None}}),
        ("launch", {"launchRun": {"__typename": "LaunchRunSuccess", "run": "broken"}}),
        ("status", {"runOrError": {"__typename": "Run", "status": None}}),
        ("terminate", {"terminateRun": "broken"}),
    ],
)
def test_dagster_malformed_response_has_safe_failure_category(
    monkeypatch, operation, payload
):
    client = DagsterClient("http://example.test/graphql")
    monkeypatch.setattr(client, "_execute", lambda *args, **kwargs: payload)
    with pytest.raises(DagsterUnavailable) as failed:
        getattr(client, operation)("test-id")
    assert failed.value.failure_type == "invalid_response"


def test_dagster_connection_reset_is_dependency_failure(monkeypatch):
    from sos_platform import dagster_client

    def reset_connection(*args, **kwargs):
        raise ConnectionResetError("connection closed")

    monkeypatch.setattr(dagster_client, "urlopen", reset_connection)
    with pytest.raises(DagsterUnavailable) as failed:
        DagsterClient("http://example.test/graphql").find_by_submission("test-id")
    assert failed.value.operation == "run_lookup"
    assert failed.value.failure_type == "connection"


def test_job_workflow_source_is_owned_by_sos_without_orchestrator():
    """실제 SQL로 출처·소유자를 확인하며 특정 Pipeline의 단계명에 종속되지 않는다."""
    from sqlalchemy import create_engine, text
    from sos_platform.store import Store

    store = object.__new__(Store)
    store.engine = create_engine('sqlite://')
    try:
        with store.connection() as db:
            db.execute(text('CREATE TABLE job_submissions (job_id TEXT, actor_id TEXT, submission_key TEXT)'))
            db.execute(text('CREATE TABLE workflow_submissions (id TEXT, owner_actor TEXT, definition_id TEXT)'))
            db.execute(text("INSERT INTO workflow_submissions VALUES ('run','owner','custom-pipeline')"))
            db.execute(text("INSERT INTO job_submissions VALUES ('job','owner','workflow:run:custom_stage'), ('ordinary','owner','request-1'), ('forged','other','workflow:run:training')"))
        assert store.job_workflow_source({'id':'job','owner_actor':'owner'}) == {
            'submission_id':'run','stage':'custom_stage','definition_id':'custom-pipeline',
        }
        assert store.job_workflow_source({'id':'ordinary','owner_actor':'owner'}) is None
        assert store.job_workflow_source({'id':'forged','owner_actor':'other'}) is None
        assert store.job_workflow_source({'id':'job','owner_actor':'other'}) is None
    finally:
        store.engine.dispose()
