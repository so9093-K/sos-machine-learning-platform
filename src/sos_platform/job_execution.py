"""등록된 Job 연산의 실행과 도메인 실패 해석. 프로세스 생명주기는 소유하지 않는다."""

import os
from collections.abc import Callable
from pathlib import Path

from .errors import PlatformError
from .execution_budget import ExecutionLimitExceeded
from .store import Store


def execute_job(store: Store, job: dict, pending: Path, set_stage: Callable[[str], None]) -> dict:
    """이미 격리·예약된 자식에서 입력을 읽고 선택한 연산만 실행한다."""

    def mark_commit_started() -> None:
        set_stage("iceberg_commit")

    dataset = store.get_dataset(job["dataset_id"]) if job["dataset_id"] else None
    if dataset is None and not (
        job["kind"] == "ml_playground"
        or (job["kind"] == "ml_explain" and job["spec"].get("playground_job_id"))
    ):
        raise ValueError("Input dataset is missing")
    if job["kind"] == "ml_playground":
        from .ml_playground import run_playground_scoring

        set_stage("processing")
        result = run_playground_scoring(store, job["spec"])
    elif job["kind"] == "profile":
        from .discovery import profile as discovery_profile
        from .understanding import build_understanding

        set_stage("processing")
        if job["spec"].get("mode") == "understanding":
            result = build_understanding(store, dataset, scope=job["spec"].get("scope", "sample"))
        else:
            result = discovery_profile(store, dataset, sample=None)
    elif job["kind"] == "exploration":
        from .discovery import explore as discovery_explore
        from .schemas import ExplorationSpec

        set_stage("processing")
        spec = ExplorationSpec.model_validate(job["spec"]["exploration"])
        result = discovery_explore(
            store, dataset, spec.column, spec.metric, spec.group_by, scope="full"
        )
    elif job["kind"] == "transform":
        from .datasets import pinned_scan_dataset
        from .observation_preparation import materialize_observation_ids
        from .processing import transform
        from .schemas import Transform

        set_stage("processing")
        spec = Transform.model_validate(job["spec"]["transform"])
        with pinned_scan_dataset(store, dataset) as source:
            if spec.observation_id_column is not None:
                evidence = materialize_observation_ids(
                    source, pending, column=spec.observation_id_column
                )
            else:
                lf = transform(source, spec)
                lf.sink_parquet(pending)
                evidence = {}
        result = {
            "input_dataset_id": dataset["id"],
            "input_dataset_version": dataset["version"],
            **evidence,
        }
    elif job["kind"] == "feature_register":
        from .feast_features import register_feature_service, verify_pinned_owner

        set_stage("processing")
        verify_pinned_owner(dataset, job["spec"], "feature_source_owner")
        result = register_feature_service(store, dataset, job["spec"])
    elif job["kind"] == "feature_retire":
        from .feast_features import retire_feature_service, verify_pinned_owner

        set_stage("processing")
        registration = store.get_job(job["spec"]["registration_job_id"])
        if (
            registration is None
            or registration["kind"] != "feature_register"
            or registration["status"] not in ("Succeeded", "Failed", "Cancelled")
            or registration["owner_actor"] != job["owner_actor"]
            or registration["dataset_id"] != dataset["id"]
        ):
            raise ValueError("등록 폐기 대상의 상태·소유권이 바뀌었습니다.")
        verify_pinned_owner(dataset, job["spec"], "feature_source_owner")
        result = retire_feature_service(store, dataset, registration)
    elif job["kind"] == "feature_retrieve":
        from .feast_features import retrieve_features, verify_pinned_owner

        set_stage("processing")
        registered_job = store.get_job(job["spec"]["registration_job_id"])
        entity = store.get_dataset(job["spec"]["entity_dataset_id"])
        if (
            registered_job is None
            or registered_job["kind"] != "feature_register"
            or registered_job["status"] != "Succeeded"
            or registered_job["dataset_id"] != dataset["id"]
            or registered_job["owner_actor"] != job["owner_actor"]
            or entity is None
        ):
            raise ValueError("Registered Feature Service or Entity Dataset is unavailable")
        # 조회 Job은 등록 행 잠금 아래에서 폐기 접수 전에 승인된다.
        # 폐기 이후 신규 제출은 Store에서 거부하지만, 그 이전에 Queued였던
        # 조회는 보존된 Feast 정의로 완료해야 하므로 실행 시점에 재차 거부하지 않는다.
        verify_pinned_owner(dataset, job["spec"], "feature_source_owner")
        verify_pinned_owner(entity, job["spec"], "entity_dataset_owner")
        result = retrieve_features(
            store, dataset, entity, registered_job["result"], job["spec"], pending
        )
    elif job["kind"] == "ml_pipeline_preflight":
        from .pipeline_reference import inspect_dataset as inspect_pipeline_dataset

        set_stage("processing")
        if job["spec"]["dataset_version"] != dataset["version"]:
            raise ValueError("Pipeline Dataset 판본이 변경됐습니다.")
        inspection = inspect_pipeline_dataset(store, dataset, full=True)
        if not inspection["ready"] or not inspection["plan"]:
            raise ValueError("Pipeline 입력 데이터의 시간/Feature 적합성 검사에 실패했습니다.")
        result = {"plan": inspection["plan"], "feature_count": inspection["feature_count"]}
    elif job["kind"] == "ml_train":
        from .ml_training import train_dataset
        from .ml_training_staging import training_stage_path

        set_stage("processing")
        # 정렬 Spill 파일은 이 Attempt만 소유한다. 종료 후 부모가 정리하고
        # 자식 강제 종료 시에도 다음 Worker의 복구 경로에서 회수한다.
        scratch = training_stage_path(store.home, job["attempts"][-1]["id"])
        if scratch.parent.is_symlink():
            raise OSError("Training stage root must not be a symlink")
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.mkdir(mode=0o700)
        os.environ["POLARS_TEMP_DIR"] = str(scratch)
        result = train_dataset(store, dataset, job["spec"], pending.with_suffix(".joblib"))
    elif job["kind"] == "ml_score":
        from .ml_scoring import run_dataset_scoring

        set_stage("processing")
        result = run_dataset_scoring(
            store, dataset, job["spec"], pending.with_suffix(".scores.parquet")
        )
    elif job["kind"] == "ml_policy":
        from .ml_decision import apply_scoring_policy

        set_stage("processing")
        result = apply_scoring_policy(store, job["spec"])
    elif job["kind"] == "ml_evaluate":
        from .ml_decision import evaluate_scoring_policy

        set_stage("processing")
        result = evaluate_scoring_policy(store, job["spec"])
    elif job["kind"] == "ml_explain":
        from .ml_explanation import explain_scored_row

        set_stage("processing")
        result = explain_scored_row(store, job["spec"])
    elif job["kind"] == "iceberg_import":
        from .datasets import import_to_iceberg

        set_stage("iceberg_prepare")
        result = {
            "iceberg_publish": import_to_iceberg(
                store,
                dataset,
                job["spec"]["table_name"],
                job["attempts"][-1]["id"],
                owner_actor=job["owner_actor"],
                on_commit_start=mark_commit_started,
            )
        }
    else:
        raise ValueError("Unknown job type")
    return result


def execution_failure_code(job: dict, exc: Exception, stage: str) -> str:
    """외부 커밋 불확실성과 입력·계산 실패를 기존 오류 계약으로 해석한다."""
    return (
        "SOS-FEATURE-INPUT-INVALID"
        if job["kind"] in ("feature_register", "feature_retrieve", "feature_retire")
        and isinstance(exc, ValueError)
        else "SOS-ML-INPUT-INVALID"
        if job["kind"]
        in (
            "ml_pipeline_preflight",
            "ml_train",
            "ml_score",
            "ml_playground",
            "ml_policy",
            "ml_evaluate",
            "ml_explain",
        )
        and isinstance(exc, ValueError)
        else "SOS-ICEBERG-UNCERTAIN"
        if job["kind"] == "iceberg_import" and stage == "iceberg_commit"
        else exc.code
        if isinstance(exc, PlatformError)
        else "SOS-JOB-FAILED"
    )


def publication_failure_code(job: dict, exc: Exception) -> str:
    """부모에서 알 수 없는 외부 커밋은 보수적으로 처리한다."""
    if job["kind"] == "iceberg_import":
        return "SOS-ICEBERG-UNCERTAIN"
    if isinstance(exc, ExecutionLimitExceeded):
        return exc.code
    return "SOS-JOB-FAILED"
