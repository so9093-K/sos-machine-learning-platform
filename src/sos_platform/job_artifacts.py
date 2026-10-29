"""Job 연산의 Attempt 파일·객체 업로드·스테이징 수명주기."""

import logging
from dataclasses import dataclass
from pathlib import Path

from .datasets import cleanup_iceberg_staging, iceberg_staging_bytes
from .execution_budget import ExecutionLimitExceeded
from .ml_scoring_staging import cleanup_scoring_stage, scoring_stage_bytes
from .ml_training_staging import cleanup_training_stage, training_stage_bytes
from .object_store import ObjectStore
from .observability import emit
from .store import Store


def attempt_result_paths(store: Store, job_id: str, attempt_id: str) -> tuple[Path, Path, Path]:
    """DB에서 발급한 Attempt마다 별도 결과 경로를 사용한다."""
    root = store.home / "results"
    stem = f"{job_id}.{attempt_id}"
    return (
        root / f"{stem}.parquet",
        root / f"{stem}.pending.parquet",
        root / f"{stem}.completed.json",
    )


@dataclass
class ResultPublication:
    """외부 업로드가 실패해도 부모가 회수할 게시 의도를 보존한다."""

    result: dict | None = None
    payload: dict | None = None
    write_id: str | None = None

    def prepare(self, store: Store, job: dict, value: dict, pending: Path, output: Path) -> None:
        """연산별 결과를 게시 입력으로 바꾸고 필요한 객체만 업로드한다."""
        self.result = {key: item for key, item in value.items() if key != "iceberg_publish"}
        self.payload = value.get("iceberg_publish")
        stem = f"results/{job['id']}/{job['attempts'][-1]['id']}"
        if job["kind"] in ("ml_train", "ml_score"):
            training = job["kind"] == "ml_train"
            artifact = pending.with_suffix(".joblib" if training else ".scores.parquet")
            if not artifact.is_file():
                raise RuntimeError("Execution completed without its model or score artifact")
            key = stem + (".model.joblib" if training else ".scores.parquet")
            self.payload = {**value, **self._upload(store, job, key, artifact)}
        elif pending.exists():
            pending.replace(output)
            uploaded = self._upload(store, job, stem + ".parquet", output)
            self.payload = {
                "name": f"features-{job['id'][:8]}.parquet",
                "kind": "file",
                "location": uploaded["location"],
                "version": uploaded["sha256"],
            }

    def _upload(self, store: Store, job: dict, key: str, path: Path) -> dict:
        storage = ObjectStore()
        location = storage.location(key)
        self.write_id = store.begin_object_write(
            location=location,
            owner_kind="attempt",
            owner_id=job["attempts"][-1]["id"],
        )
        # 실패 시에도 write_id는 유지된다. 외부 성공과 DB 게시를 혼동하지 않는다.
        return {"location": location, "sha256": storage.put(key, path)}


def enforce_stage_limit(store: Store, job: dict, pending: Path, max_stage_mib: int) -> None:
    """연산별 임시 파일을 합산하고 외부 커밋 위험에 맞는 실패 코드를 사용한다."""
    kind, attempt_id = job["kind"], job["attempts"][-1]["id"]
    if kind == "iceberg_import":
        size = iceberg_staging_bytes(store, attempt_id)
        code = "SOS-ICEBERG-UNCERTAIN"
    elif kind == "ml_train":
        size = training_stage_bytes(store.home, attempt_id)
        code = "SOS-ML-TRAIN-STAGE-LIMIT"
    elif kind == "ml_score":
        size = scoring_stage_bytes(store.home, pending.with_suffix(".scores.parquet"))
        code = "SOS-ML-SCORE-STAGE-LIMIT"
    else:
        return
    if size > max_stage_mib * 1024 * 1024:
        raise ExecutionLimitExceeded(code, "storage")


def cleanup_recovered_results(store: Store, interrupted: list[tuple[str, str]]) -> None:
    """복구가 확정된 Attempt 파일만 회수하며 공유 객체를 삭제하지 않는다."""
    for job_id, attempt_id in interrupted:
        output, pending, completed = attempt_result_paths(store, job_id, attempt_id)
        _remove_result_files(output, pending, completed)
        cleanup_scoring_stage(store.home, pending.with_suffix(".scores.parquet"))


def _remove_result_files(output: Path, pending: Path, completed: Path) -> None:
    for path in (
        output,
        pending,
        completed,
        pending.with_suffix(".joblib"),
        pending.with_suffix(".scores.parquet"),
    ):
        path.unlink(missing_ok=True)


def cleanup_attempt_files(store: Store, job: dict) -> None:
    """종료 후 파일 회수 실패가 확정된 Job 상태를 덮어쓰지 않게 한다."""
    job_id, attempt_id = job["id"], job["attempts"][-1]["id"]
    output, pending, completed = attempt_result_paths(store, job_id, attempt_id)
    try:
        _remove_result_files(output, pending, completed)
        if job["kind"] == "ml_score":
            cleanup_scoring_stage(store.home, pending.with_suffix(".scores.parquet"))
        elif job["kind"] == "ml_train":
            cleanup_training_stage(store.home, attempt_id)
        elif job["kind"] == "iceberg_import":
            cleanup_iceberg_staging(store, attempt_id)
    except (OSError, ValueError):
        emit(
            "worker.staging.cleanup_failed",
            service="sos-worker",
            level=logging.ERROR,
            job_id=job_id,
            attempt_id=attempt_id,
        )


def cleanup_recovered_staging(store: Store) -> None:
    """이전 자식이 없는 단일 Worker 잠금 아래에서만 공용 스테이징을 회수한다."""
    cleanup_iceberg_staging(store)
    cleanup_training_stage(store.home)
