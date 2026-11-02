"""Job 연산의 Attempt 파일·객체 업로드·스테이징 수명주기."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .datasets import cleanup_iceberg_staging, iceberg_staging_bytes
from .execution_budget import ExecutionLimitExceeded
from .ml_training_staging import cleanup_training_stage, training_stage_bytes
from .model_artifacts import JOBLIB, SKOPS, model_artifact_key
from .object_store import ObjectStore
from .observability import emit
from .result_staging import cleanup_result_stage, result_parts_dir, result_stage_bytes
from .scoring_index import part_files
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
    part_write_ids: list[str] = field(default_factory=list)

    def write_ids(self) -> list[str]:
        """이 게시가 만든 모든 객체 게시 의도(주 결과와 관측값 색인 조각)."""
        return ([self.write_id] if self.write_id else []) + self.part_write_ids

    def prepare(self, store: Store, job: dict, value: dict, pending: Path, output: Path) -> None:
        """연산별 결과를 게시 입력으로 바꾸고 필요한 객체만 업로드한다."""
        self.result = {key: item for key, item in value.items() if key != "iceberg_publish"}
        self.payload = value.get("iceberg_publish")
        attempt_id = job["attempts"][-1]["id"]
        stem = f"results/{job['id']}/{attempt_id}"
        # 모델 판본의 아티팩트 위치는 형식별 규칙(model_artifacts)을 게시·복원·삭제가 함께 쓴다.
        outputs = {
            "ml_train": (".joblib", model_artifact_key(job["id"], attempt_id, JOBLIB)),
            "ml_import": (".skops", model_artifact_key(job["id"], attempt_id, SKOPS)),
            "ml_score": (".scores.parquet", stem + ".scores.parquet"),
        }
        if job["kind"] in outputs:
            suffix, key = outputs[job["kind"]]
            artifact = pending.with_suffix(suffix)
            if not artifact.is_file():
                raise RuntimeError("Execution completed without its model or score artifact")
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
        if job["kind"] == "ml_score":
            # 관측값 색인 조각은 결과마다 한 번 만든 불변 객체로 게시한다. Scoring은 항상 색인을 만든다.
            directory = result_parts_dir(result_stage_owner(job["kind"], pending))
            if not directory.is_dir():
                raise ValueError("Scoring result has no observation index")
            parts = []
            for part in part_files(directory):
                key = f"{stem}.observations/{part['path'].name}"
                uploaded = self._upload_part(store, job, key, part["path"])
                parts.append({key: value for key, value in part.items() if key != "path"} | uploaded)
            self.payload = {**(self.payload or value), "observation_parts": parts}

    def _upload_part(self, store: Store, job: dict, key: str, path: Path) -> dict:
        storage = ObjectStore()
        location = storage.location(key)
        write_id = store.begin_object_write(
            location=location, owner_kind="attempt", owner_id=job["attempts"][-1]["id"],
        )
        # 업로드가 실패해도 게시 의도는 남겨 부모가 실패로 기록한다.
        self.part_write_ids.append(write_id)
        return {"location": location, "sha256": storage.put(key, path), "write_id": write_id}

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


# 결과 Stage(`result_staging`)를 쓰는 연산과 Stage 한도를 넘었을 때의 오류.
_RESULT_STAGE_LIMITS = {
    "ml_score": "SOS-ML-SCORE-STAGE-LIMIT",
    "ml_policy": "SOS-ML-SCORE-STAGE-LIMIT",
    "ml_evaluate": "SOS-ML-SCORE-STAGE-LIMIT",
    "ml_explain": "SOS-ML-SCORE-STAGE-LIMIT",
    "feature_retrieve": "SOS-FEATURE-STAGE-LIMIT",
}


def result_stage_owner(kind: str, pending: Path) -> Path:
    """Scoring은 결과 파일, 그 밖의 연산은 Attempt 결과 경로가 Stage를 소유한다."""
    return pending.with_suffix(".scores.parquet") if kind == "ml_score" else pending


def enforce_stage_limit(store: Store, job: dict, pending: Path, max_stage_mib: int) -> None:
    """연산별 임시 파일을 합산하고 외부 커밋 위험에 맞는 실패 코드를 사용한다."""
    kind, attempt_id = job["kind"], job["attempts"][-1]["id"]
    if kind == "iceberg_import":
        size = iceberg_staging_bytes(store, attempt_id)
        code = "SOS-ICEBERG-UNCERTAIN"
    elif kind == "ml_train":
        size = training_stage_bytes(store.home, attempt_id)
        code = "SOS-ML-TRAIN-STAGE-LIMIT"
    elif kind in _RESULT_STAGE_LIMITS:
        size = result_stage_bytes(result_stage_owner(kind, pending))
        code = _RESULT_STAGE_LIMITS[kind]
    else:
        return
    if size > max_stage_mib * 1024 * 1024:
        raise ExecutionLimitExceeded(code, "storage")


def cleanup_recovered_results(store: Store, interrupted: list[tuple[str, str]]) -> None:
    """복구가 확정된 모든 Attempt의 회수를 시도하고 실패를 항목별로 보고한다."""
    for job_id, attempt_id in interrupted:
        output, pending, completed = attempt_result_paths(store, job_id, attempt_id)
        failures = _remove_result_files(output, pending, completed)
        # 복구 시점에는 Job 종류를 묻지 않는다. 두 소유 경로의 Stage를 모두 회수한다.
        for owner in (pending.with_suffix(".scores.parquet"), pending):
            try:
                cleanup_result_stage(owner)
            except (OSError, ValueError) as exc:
                failures.append(("result_stage", exc))
        _report_cleanup_failures(failures, job_id=job_id, attempt_id=attempt_id)


def _remove_result_files(
    output: Path,
    pending: Path,
    completed: Path,
) -> list[tuple[str, Exception]]:
    failures = []
    for operation, path in (
        ("result.output", output),
        ("result.pending", pending),
        ("result.completed", completed),
        ("result.model", pending.with_suffix(".joblib")),
        ("result.imported_model", pending.with_suffix(".skops")),
        ("result.scores", pending.with_suffix(".scores.parquet")),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            failures.append((operation, exc))
    return failures


def _report_cleanup_failures(
    failures: list[tuple[str, Exception]],
    *,
    job_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    # 파일 경로·예외 원문 대신 Attempt와 고정 항목명으로 실패 대상을 식별한다.
    for operation, exc in failures:
        emit(
            "worker.staging.cleanup_failed",
            service="sos-worker",
            level=logging.ERROR,
            job_id=job_id,
            attempt_id=attempt_id,
            operation=operation,
            exception_type=type(exc).__name__,
        )


def cleanup_attempt_files(store: Store, job: dict) -> None:
    """파일별 실패와 관계없이 스테이징 회수까지 시도한 뒤 실패 항목을 보고한다."""
    job_id, attempt_id = job["id"], job["attempts"][-1]["id"]
    output, pending, completed = attempt_result_paths(store, job_id, attempt_id)
    failures = _remove_result_files(output, pending, completed)
    operation = "staging"
    try:
        if job["kind"] in _RESULT_STAGE_LIMITS:
            operation = "result_stage"
            cleanup_result_stage(result_stage_owner(job["kind"], pending))
        elif job["kind"] == "ml_train":
            operation = "training_stage"
            cleanup_training_stage(store.home, attempt_id)
        elif job["kind"] == "iceberg_import":
            operation = "iceberg_stage"
            cleanup_iceberg_staging(store, attempt_id)
    except (OSError, ValueError) as exc:
        failures.append((operation, exc))
    _report_cleanup_failures(failures, job_id=job_id, attempt_id=attempt_id)


def cleanup_recovered_staging(store: Store) -> None:
    """단일 Worker 잠금 아래 두 스테이징의 회수를 각각 시도하고 실패를 보고한다."""
    failures = []
    for operation, cleanup in (
        ("iceberg_stage", lambda: cleanup_iceberg_staging(store)),
        ("training_stage", lambda: cleanup_training_stage(store.home)),
    ):
        try:
            cleanup()
        except (OSError, ValueError) as exc:
            failures.append((operation, exc))
    _report_cleanup_failures(failures)
