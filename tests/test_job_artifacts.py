"""업로드 응답 소실과 개별 정리 실패가 Attempt 수명주기를 깨지 않아야 한다."""

from unittest.mock import Mock

import pytest

from sos_platform import job_artifacts


def test_upload_failure_keeps_write_intent_for_parent_cleanup(tmp_path, monkeypatch):
    pending = tmp_path / "pending.parquet"
    pending.with_suffix(".joblib").write_bytes(b"model")
    store = Mock()
    store.begin_object_write.return_value = "write-1"
    storage = Mock()
    storage.location.return_value = "s3://test/results/job/attempt.model.joblib"
    storage.put.side_effect = OSError("lost upload response")
    monkeypatch.setattr(job_artifacts, "ObjectStore", lambda: storage)
    publication = job_artifacts.ResultPublication()
    with pytest.raises(OSError):
        publication.prepare(
            store,
            {"id": "job", "kind": "ml_train", "attempts": [{"id": "attempt"}]},
            {"training_rows": 2},
            pending,
            tmp_path / "result.parquet",
        )
    assert publication.write_id == "write-1"
    assert publication.payload is None
    assert publication.result == {"training_rows": 2}
    store.begin_object_write.assert_called_once_with(
        location=storage.location.return_value,
        owner_kind="attempt",
        owner_id="attempt",
    )


@pytest.mark.parametrize(
    "kind,cleanup_name,operation",
    [
        ("ml_train", "cleanup_training_stage", "training_stage"),
        ("ml_score", "cleanup_scoring_stage", "scoring_stage"),
        ("iceberg_import", "cleanup_iceberg_staging", "iceberg_stage"),
    ],
)
def test_cleanup_attempts_all_files_and_staging_before_reporting(
    tmp_path,
    monkeypatch,
    kind,
    cleanup_name,
    operation,
):
    from pathlib import Path
    from types import SimpleNamespace

    store = SimpleNamespace(home=tmp_path)
    (tmp_path / "results").mkdir()
    output, pending, completed = job_artifacts.attempt_result_paths(store, "job", "attempt")
    model, scores = pending.with_suffix(".joblib"), pending.with_suffix(".scores.parquet")
    files = (output, pending, completed, model, scores)
    for path in files:
        path.write_bytes(b"staged")
    attempted = []
    unlink = Path.unlink

    def failing_unlink(path, **kwargs):
        attempted.append(path)
        if path in (output, model):
            raise PermissionError("private filesystem details")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    cleanup = Mock(side_effect=OSError("private stage details"))
    monkeypatch.setattr(job_artifacts, cleanup_name, cleanup)
    reports = []

    def report(_event, **fields):
        assert attempted == list(files)
        cleanup.assert_called_once()
        reports.append(fields)

    monkeypatch.setattr(job_artifacts, "emit", report)
    job_artifacts.cleanup_attempt_files(
        store,
        {"id": "job", "kind": kind, "attempts": [{"id": "attempt"}]},
    )
    assert [item["operation"] for item in reports] == ["result.output", "result.model", operation]
    assert all(item["job_id"] == "job" and item["attempt_id"] == "attempt" for item in reports)
    assert [item["exception_type"] for item in reports] == [
        "PermissionError",
        "PermissionError",
        "OSError",
    ]
    assert output.exists() and model.exists()
    assert not any(path.exists() for path in (pending, completed, scores))


def test_recovery_continues_to_next_attempt_after_file_and_stage_failure(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    store = SimpleNamespace(home=tmp_path)
    (tmp_path / "results").mkdir()
    first = job_artifacts.attempt_result_paths(store, "job", "first")
    second = job_artifacts.attempt_result_paths(store, "job", "second")
    for path in (*first, *second):
        path.write_bytes(b"staged")
    untouched = tmp_path / "results" / "unrelated.pending.parquet"
    untouched.write_bytes(b"other owner")
    unlink = Path.unlink

    def failing_unlink(path, **kwargs):
        if path == first[1]:
            raise PermissionError("cannot remove pending")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    cleanup = Mock(side_effect=[OSError("cannot remove stage"), None])
    monkeypatch.setattr(job_artifacts, "cleanup_scoring_stage", cleanup)
    reports = Mock()
    monkeypatch.setattr(job_artifacts, "emit", reports)
    job_artifacts.cleanup_recovered_results(store, [("job", "first"), ("job", "second")])
    assert cleanup.call_count == 2
    assert first[1].exists() and not first[0].exists() and not first[2].exists()
    assert not any(path.exists() for path in second)
    assert untouched.exists()
    assert [call.kwargs["operation"] for call in reports.call_args_list] == [
        "result.pending",
        "scoring_stage",
    ]


def test_recovered_training_cleanup_runs_after_iceberg_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    iceberg = Mock(side_effect=OSError("unavailable stage"))
    training = Mock()
    monkeypatch.setattr(job_artifacts, "cleanup_iceberg_staging", iceberg)
    monkeypatch.setattr(job_artifacts, "cleanup_training_stage", training)
    reports = Mock()
    monkeypatch.setattr(job_artifacts, "emit", reports)
    job_artifacts.cleanup_recovered_staging(SimpleNamespace(home=tmp_path))
    iceberg.assert_called_once()
    training.assert_called_once_with(tmp_path)
    assert reports.call_args.kwargs["operation"] == "iceberg_stage"
