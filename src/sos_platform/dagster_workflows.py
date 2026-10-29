"""Workflow 제출과 Dagster Run 연결. SOS에 별도의 단계 전이 엔진을 만들지 않는다."""
import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from .dagster_client import DagsterClient, DagsterUnavailable
from .pipeline_reference import PIPELINE_ID, PIPELINE_VERSION, STAGES
from .store_administration import now


def workflow_job_key(submission_id: str, stage: str) -> str:
    """Workflow 단계 Job의 멱등 제출 키. Dagster 재실행도 같은 SOS Job을 재사용한다."""
    return f"workflow:{submission_id}:{stage}"


def _submission(store, submission_id: str) -> dict | None:
    with store.connection() as db:
        row = db.execute(
            text("SELECT * FROM workflow_submissions WHERE id=:id"),
            {"id": submission_id},
        ).mappings().one_or_none()
    return dict(row) if row else None


def _job_by_stage(store, row: dict, stage: str) -> dict | None:
    with store.connection() as db:
        job_id = db.execute(text(
            "SELECT job_id FROM job_submissions "
            "WHERE actor_id=:actor AND submission_key=:key"
        ), {
            "actor": row["owner_actor"],
            "key": workflow_job_key(row["id"], stage),
        }).scalar_one_or_none()
    return store.get_job(job_id) if job_id else None


def _run(store, row: dict, *, include_jobs: bool = True, client: DagsterClient | None = None) -> dict:
    client = client or DagsterClient()
    run_id = row["dagster_run_id"]
    if not run_id:
        # 이전 실행 접수의 네트워크 응답 유실을 복구할 수 있게 연결을 조회한다.
        run_id = client.find_by_submission(row["id"])
    raw_status = client.status(run_id) if run_id else (
        "CANCELED" if row.get("cancel_requested") else "NOT_STARTED"
    )
    statuses = {
        "NOT_STARTED": "Queued", "QUEUED": "Queued",
        "STARTING": "Running", "STARTED": "Running", "MANAGED": "Running",
        "CANCELING": "Running", "SUCCESS": "Succeeded",
        "FAILURE": "Failed", "CANCELED": "Cancelled",
    }
    if raw_status not in statuses:
        raise DagsterUnavailable(
            "Dagster에서 알 수 없는 실행 상태를 반환했습니다.",
            operation="run_status", failure_type="unexpected_result",
        )
    jobs = {stage: _job_by_stage(store, row, stage) for stage in STAGES}
    created = {name: item for name, item in jobs.items() if item is not None}
    preflight = jobs["preflight"]
    plan = (preflight.get("result") or {}).get("plan") if preflight else None
    current = None
    if statuses[raw_status] in ("Queued", "Running") and not row.get("cancel_requested"):
        for stage in STAGES:
            job = jobs[stage]
            if not job or job["status"] != "Succeeded":
                current = stage
                break
            if stage == "training":
                model_id = (job.get("result") or {}).get("model_revision_id")
                model = store.get_model_revision(model_id) if model_id else None
                if not model or not model.get("registry_version"):
                    current = stage
                    break
    error = next((
        job.get("error_code") for job in created.values() if job["status"] == "Failed"
    ), None)
    if statuses[raw_status] == "Failed" and error is None:
        training = jobs["training"]
        if training and training["status"] == "Succeeded":
            model_id = (training.get("result") or {}).get("model_revision_id")
            model = store.get_model_revision(model_id) if model_id else None
            if model is None:
                error = "SOS-ML-MODEL-INVALID"
            elif not model.get("registry_version") and store.tracking_status(training["id"]) is None:
                # 학습 Job은 성공해도 Registry가 구성되지 않으면 후속 Scoring 불가.
                error = "SOS-MLFLOW-NOT-CONFIGURED"
        error = error or "SOS-JOB-FAILED"
    return {
        "id": row["id"],
        "definition_id": row["definition_id"],
        "definition_version": row["definition_version"],
        "dataset_id": row["dataset_id"],
        "dataset_version": row["dataset_version"],
        "owner_actor": row["owner_actor"],
        "input_plan": plan,
        "status": statuses[raw_status],
        "cancel_requested": statuses[raw_status] in ("Queued", "Running") and (
            bool(row.get("cancel_requested")) or raw_status == "CANCELING"
        ),
        "error_code": error,
        "created_at": row["created_at"],
        "current_stage": current,
        "stages": [
            {"name": name, "job": job} for name, job in created.items()
        ] if include_jobs else [],
    }


def create_run(store, dataset: dict, owner_actor: str, inspection: dict,
               submission_key: str | None, *, actor_type: str = "human") -> dict:
    """동일 Actor·요청으로 한 Dagster Run만 시작한다.

    Dagster 외부 호출 성공 후 응답 소실 시 태그 검색으로 기존 Run을 복구한다.
    """
    if actor_type not in ("human", "service") or not inspection["ready"]:
        raise ValueError("잘못된 Workflow 제출 요청입니다.")
    plan = inspection["plan"]
    fingerprint = hashlib.sha256(json.dumps({
        "definition_id": PIPELINE_ID,
        "definition_version": PIPELINE_VERSION,
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "plan": plan,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = submission_key or str(uuid4())
    submission_id = str(uuid4())
    with store.connection() as db:
        db.execute(text(
            "INSERT INTO workflow_submissions "
            "(id,actor_id,submission_key,fingerprint,definition_id,definition_version,"
            "dataset_id,dataset_version,owner_actor,owner_actor_type,created_at) "
            "VALUES (:id,:actor,:key,:fingerprint,:definition_id,:version,"
            ":dataset_id,:dataset_version,:actor,:type,:now) "
            "ON CONFLICT(actor_id,submission_key) DO NOTHING"
        ), {
            "id": submission_id, "actor": owner_actor, "key": key,
            "fingerprint": fingerprint, "definition_id": PIPELINE_ID,
            "version": PIPELINE_VERSION, "dataset_id": dataset["id"],
            "dataset_version": dataset["version"], "type": actor_type, "now": now(),
        })
        row = db.execute(text(
            "SELECT * FROM workflow_submissions WHERE actor_id=:actor AND submission_key=:key"
        ), {"actor": owner_actor, "key": key}).mappings().one()
        if row["fingerprint"] != fingerprint:
            raise ValueError("같은 Idempotency-Key에 다른 Workflow 입력을 사용할 수 없습니다.")
        submission_id = row["id"]
    client = DagsterClient()
    # DB의 트랜잭션 advisory lock이 같은 요청의 경쟁 제출을 직렬화한다.
    # Dagster는 PostgreSQL과 원자 트랜잭션을 공유하지 않으므로 실행 태그도 대조한다.
    with store.connection() as db:
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": "sos-workflow:" + submission_id},
        )
        run_id = db.execute(text(
            "SELECT dagster_run_id FROM workflow_submissions WHERE id=:id FOR UPDATE"
        ), {"id": submission_id}).scalar_one()
        if not run_id:
            # 취소된 접수는 Dagster Run을 새로 생성하지 않는다.
            cancelled = db.execute(text(
                "SELECT cancel_requested FROM workflow_submissions WHERE id=:id"
            ), {"id": submission_id}).scalar_one()
            if not cancelled:
                run_id = client.find_by_submission(submission_id) or client.launch(submission_id)
                db.execute(text(
                    "UPDATE workflow_submissions SET dagster_run_id=:run WHERE id=:id"
                ), {"run": run_id, "id": submission_id})
        store._audit(
            db, actor_id=owner_actor, actor_type=actor_type,
            action="workflow.submit", resource_type="workflow",
            resource_id=submission_id,
        )
    return get_run(store, submission_id)


def get_run(store, submission_id: str, *, include_jobs: bool = True) -> dict | None:
    row = _submission(store, submission_id)
    return _run(store, row, include_jobs=include_jobs) if row else None


def list_runs(store, *, actor_id: str | None, limit: int = 30) -> list[dict]:
    with store.connection() as db:
        if actor_id is None:
            rows = db.execute(text(
                "SELECT * FROM workflow_submissions ORDER BY created_at DESC,id DESC LIMIT :n"
            ), {"n": limit}).mappings().all()
        else:
            rows = db.execute(text(
                "SELECT * FROM workflow_submissions WHERE owner_actor=:actor "
                "ORDER BY created_at DESC,id DESC LIMIT :n"
            ), {"actor": actor_id, "n": limit}).mappings().all()
    client = DagsterClient()
    return [_run(store, dict(row), include_jobs=False, client=client) for row in rows]


def cancel_run(store, submission_id: str) -> dict | None:
    row = _submission(store, submission_id)
    if row is None:
        return None
    # 접수만 완료된 요청에도 취소 의도를 보존한다. 실제 Run 상태는 Dagster 정본이다.
    with store.connection() as db:
        db.execute(text(
            "UPDATE workflow_submissions SET cancel_requested=TRUE WHERE id=:id"
        ), {"id": submission_id})
    client = DagsterClient()
    run_id = row["dagster_run_id"] or client.find_by_submission(submission_id)
    if run_id is not None:
        status = client.status(run_id)
        if status not in ("SUCCESS", "FAILURE", "CANCELED"):
            # 먼저 SOS Job에 취소 의사를 전달하고 Dagster 조정 프로세스를 종료한다.
            for stage in STAGES:
                job = _job_by_stage(store, row, stage)
                if job and job["status"] in ("Queued", "Running"):
                    store.request_cancel(
                        job["id"], actor_id=row["owner_actor"],
                        actor_type=row["owner_actor_type"],
                    )
            client.terminate(run_id)
    result = get_run(store, submission_id)
    if result and result["status"] in ("Queued", "Running"):
        result["cancel_requested"] = True
    return result
