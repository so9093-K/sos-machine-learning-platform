"""외부 업로드 실패 이후에도 게시 의도를 회수할 수 있어야 한다."""

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
            store, {"id": "job", "kind": "ml_train", "attempts": [{"id": "attempt"}]},
            {"training_rows": 2}, pending, tmp_path / "result.parquet",
        )
    assert publication.write_id == "write-1"
    assert publication.payload is None
    assert publication.result == {"training_rows": 2}
    store.begin_object_write.assert_called_once_with(
        location=storage.location.return_value, owner_kind="attempt", owner_id="attempt",
    )
