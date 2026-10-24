"""등록형 Pipeline Run 상태와 독립 ML Job 조합.

신뢰된 Worker의 단일 실행 잠금 아래에서 다음 Job만 제출한다.
학습·Scoring·정책·평가는 여전히 각각 독립 Job이다.
"""
import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from .pipeline_reference import PIPELINE_ID, PIPELINE_VERSION, FEATURES, STAGES
from .store_administration import now


def _decode(row: dict) -> dict:
    item = dict(row)
    item["input_plan"] = json.loads(item["input_plan"])
    item["job_ids"] = json.loads(item["job_ids"])
    item["cancel_requested"] = bool(item["cancel_requested"])
    item["current_stage"] = (
        STAGES[item["stage_index"]] if item["stage_index"] < len(STAGES) else None
    )
    return item


def get_run(store, run_id: str, *, include_jobs: bool = True) -> dict | None:
    with store.connection() as db:
        record = db.execute(
            text("SELECT * FROM pipeline_runs WHERE id=:id"), {"id": run_id}
        ).mappings().one_or_none()
    if record is None:
        return None
    run = _decode(record)
    if include_jobs:
        run["stages"] = [
            {"name": stage, "job": store.get_job(run["job_ids"][stage])}
            for stage in STAGES if stage in run["job_ids"]
        ]
    return run


def list_runs(store, *, actor_id: str | None, limit: int = 30) -> list[dict]:
    with store.connection() as db:
        if actor_id is None:
            records = db.execute(text(
                "SELECT * FROM pipeline_runs ORDER BY created_at DESC,id DESC LIMIT :limit"
            ), {"limit": limit}).mappings().all()
        else:
            records = db.execute(text(
                "SELECT * FROM pipeline_runs WHERE owner_actor=:actor "
                "ORDER BY created_at DESC,id DESC LIMIT :limit"
            ), {"actor": actor_id, "limit": limit}).mappings().all()
    return [_decode(record) for record in records]


def create_run(store, dataset: dict, owner_actor: str, inspection: dict,
               submission_key: str | None, *, actor_type: str = "human") -> dict:
    """동일 Actor·제출 키에 같은 내용이면 같은 Run을 반환한다."""
    if actor_type not in ("human", "service"):
        raise ValueError("Unsupported Pipeline owner actor type")
    plan = inspection["plan"]
    if not inspection["ready"]:
        raise ValueError("Pipeline 입력 적합성 검사에 실패했습니다.")
    fingerprint = hashlib.sha256(json.dumps({
        "definition": PIPELINE_ID, "version": PIPELINE_VERSION,
        "dataset_id": dataset["id"], "dataset_version": dataset["version"],
        "plan": plan,
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    run_id = str(uuid4())
    key = submission_key or str(uuid4())
    with store.connection() as db:
        inserted = db.execute(text(
            "INSERT INTO pipeline_submissions(actor_id,submission_key,fingerprint,run_id) "
            "VALUES(:actor,:key,:fingerprint,:run_id) "
            "ON CONFLICT(actor_id,submission_key) DO NOTHING RETURNING run_id"
        ), {"actor": owner_actor, "key": key, "fingerprint": fingerprint,
            "run_id": run_id}).scalar_one_or_none()
        if inserted is None:
            existing = db.execute(text(
                "SELECT fingerprint,run_id FROM pipeline_submissions "
                "WHERE actor_id=:actor AND submission_key=:key"
            ), {"actor": owner_actor, "key": key}).mappings().one()
            if existing["fingerprint"] != fingerprint:
                raise ValueError("같은 Idempotency-Key에 다른 Pipeline 입력을 사용할 수 없습니다.")
            run_id = existing["run_id"]
        else:
            db.execute(text(
                "INSERT INTO pipeline_runs"
                "(id,definition_id,definition_version,dataset_id,dataset_version,"
                "owner_actor,owner_actor_type,input_plan,stage_index,job_ids,status,cancel_requested,"
                "created_at,updated_at) "
                "VALUES(:id,:definition_id,:version,:dataset_id,:dataset_version,"
                ":owner_actor,:owner_actor_type,:plan,0,'{}','Queued',FALSE,:created,:updated)"
            ), {
                "id": run_id, "definition_id": PIPELINE_ID, "version": PIPELINE_VERSION,
                "dataset_id": dataset["id"], "dataset_version": dataset["version"],
                "owner_actor": owner_actor, "owner_actor_type": actor_type,
                "plan": json.dumps(plan),
                "created": now(), "updated": now(),
            })
    return get_run(store, run_id)


def cancel_run(store, run_id: str) -> dict:
    with store.connection() as db:
        db.execute(text(
            "UPDATE pipeline_runs SET cancel_requested=TRUE,updated_at=:at "
            "WHERE id=:id AND status IN ('Queued','Running')"
        ), {"id": run_id, "at": now()})
    return get_run(store, run_id)


def _stage_job_spec(store, run: dict, stage: str) -> tuple[str, dict]:
    jobs = {name: store.get_job(job_id) for name, job_id in run["job_ids"].items()}
    dataset_version = run["dataset_version"]
    owner = run["owner_actor"]
    plan = run["input_plan"]
    if stage == "preflight":
        return "ml_pipeline_preflight", {
            "dataset_version": dataset_version,
            "definition_id": PIPELINE_ID,
            "definition_version": PIPELINE_VERSION,
        }
    if plan is None:
        raise ValueError("Pipeline 사전 검사가 완료되지 않았습니다.")
    time_scope = {
        "timestamp_column": plan["timestamp_column"],
        "train_end": plan["train_end"],
        "calibration_end": plan["calibration_end"],
    }
    if stage == "training":
        return "ml_train", {
            "algorithm": "isolation_forest",
            "dataset_version": dataset_version,
            "features": list(FEATURES),
            "params": {
                "n_estimators": 100, "max_samples": "auto", "max_features": 1.0,
                "bootstrap": False, "random_state": 42,
            },
            "training_input": {"mode": "all"},
            "missing_policy": "train_median", "temporal_split": time_scope,
        }
    if stage in ("calibration", "test"):
        model_id = jobs["training"]["result"]["model_revision_id"]
        return "ml_score", {
            "model_revision_id": model_id,
            "model_owner_actor": owner, "dataset_version": dataset_version,
            "label_column": None, "observation_id_column": None,
            "temporal_scope": {
                **time_scope, "partition": "calibration" if stage == "calibration" else "test"
            },
        }
    if stage == "policy":
        calibration = jobs["calibration"]["result"]["scoring_result_id"]
        return "ml_policy", {
            "scoring_result_id": calibration, "mode": "quantile",
            "alert_fraction": 0.005, "threshold": None, "source_owner_actor": owner,
        }
    if stage == "evaluation":
        scoring = jobs["test"]["result"]["scoring_result_id"]
        policy = jobs["policy"]["result"]["policy_revision_id"]
        return "ml_evaluate", {
            "scoring_result_id": scoring, "policy_revision_id": policy,
            "source_owner_actor": owner, "policy_owner_actor": owner,
            "reference_owner_actor": owner,
        }
    raise ValueError("Unknown Pipeline stage")


def advance_run(store) -> bool:
    """Worker 잠금 아래에서 최대 한 Run의 상태를 전진시킨다.

    Job 생성과 Run 행 업데이트 사이의 중단은 Job 제출 멱등성 키로 복구한다.
    Worker 외 경로에서는 호출하지 않는다.
    """
    store._assert_worker_ownership()
    # 대기 중인 선행 Job이 뒤의 독립 Run까지 가로막지 않도록 순회한다.
    with store.connection() as db:
        ids = db.execute(text(
            "SELECT id FROM pipeline_runs WHERE status IN ('Queued','Running') "
            "ORDER BY created_at,id"
        )).scalars().all()
    for run_id in ids:
        if _advance_one_run(store, run_id):
            return True
    return False


def _advance_one_run(store, run_id: str) -> bool:
    run = get_run(store, run_id, include_jobs=False)
    if run is None or run["status"] not in ("Queued", "Running"):
        return False
    if run["definition_id"] != PIPELINE_ID or run["definition_version"] != PIPELINE_VERSION:
        _update_run(store, run["id"], status="Failed", error_code="SOS-ML-INPUT-INVALID")
        return True
    if run["stage_index"] >= len(STAGES):
        return False
    stage = STAGES[run["stage_index"]]
    job_id = run["job_ids"].get(stage)
    if run["cancel_requested"]:
        if job_id:
            job = store.get_job(job_id)
            if job is None:
                _update_run(store, run["id"], status="Failed", error_code="SOS-JOB-FAILED")
                return True
            if job["status"] == "Failed":
                # 취소 요청이 실제로 실패한 Attempt의 원인을 덮지 않는다.
                _update_run(
                    store, run["id"], status="Failed", error_code=job.get("error_code")
                )
                return True
            if job["status"] in ("Queued", "Running") and not job["cancel_requested"]:
                store.request_cancel(
                    job_id, actor_id=run["owner_actor"],
                    actor_type=run["owner_actor_type"],
                )
                return True
            if job["status"] in ("Queued", "Running"):
                return False
        _update_run(store, run["id"], status="Cancelled")
        return True
    if not job_id:
        try:
            kind, spec = _stage_job_spec(store, run, stage)
        except (KeyError, TypeError, ValueError):
            # 앞 단계의 게시 결과가 예상한 계약과 다르면 해당 Run만 실패시킨다.
            # Worker 전체를 중지하면 다른 사용자의 정상 Job까지 멈추게 된다.
            _update_run(store, run["id"], status="Failed", error_code="SOS-JOB-FAILED")
            return True
        job = store.create_job(
            kind=kind, dataset_id=run["dataset_id"], spec=spec,
            owner_actor=run["owner_actor"],
            actor_type=run["owner_actor_type"],
            idempotency_key="pipeline:" + run["id"] + ":" + stage,
        )
        refs = {**run["job_ids"], stage: job["id"]}
        with store.connection() as db:
            db.execute(text(
                "UPDATE pipeline_runs SET job_ids=:jobs,status='Running',updated_at=:at "
                "WHERE id=:id AND status IN ('Queued','Running')"
            ), {"jobs": json.dumps(refs), "at": now(), "id":run["id"]})
        return True
    job = store.get_job(job_id)
    if job is None:
        _update_run(store, run["id"], status="Failed", error_code="SOS-JOB-FAILED")
        return True
    if job["status"] in ("Queued", "Running"):
        return False
    if job["status"] != "Succeeded":
        _update_run(store, run["id"], status=job["status"],
                    error_code=job.get("error_code"))
        return True
    if stage == "training":
        # 학습 성공과 모델 Registry 등록 완료는 서로 다른 사건이다.
        # 등록이 확정되기 전에는 Calibration/Scoring으로 전진하지 않는다.
        model_id = (job.get("result") or {}).get("model_revision_id")
        model = store.get_model_revision(model_id) if model_id else None
        if model is None:
            _update_run(store, run["id"], status="Failed", error_code="SOS-ML-MODEL-INVALID")
            return True
        if not model.get("registry_version"):
            # 관리 서버가 애초에 설정되지 않은 실행은 영원히 기다리지 않는다.
            if store.tracking_status(job_id) is None:
                _update_run(
                    store, run["id"], status="Failed",
                    error_code="SOS-MLFLOW-NOT-CONFIGURED",
                )
                return True
            return False
    if stage == "preflight":
        result = job.get("result") or {}
        plan = result.get("plan") if isinstance(result, dict) else None
        if not isinstance(plan, dict) or not all(
            key in plan for key in ("timestamp_column", "train_end", "calibration_end")
        ):
            _update_run(store, run["id"], status="Failed", error_code="SOS-ML-INPUT-INVALID")
            return True
        with store.connection() as db:
            db.execute(text(
                "UPDATE pipeline_runs SET input_plan=:plan,stage_index=1,updated_at=:at "
                "WHERE id=:id AND status='Running'"
            ), {"id": run["id"], "plan": json.dumps(plan), "at": now()})
        return True
    if run["stage_index"] + 1 == len(STAGES):
        _update_run(store, run["id"], status="Succeeded",
                    stage_index=len(STAGES))
    else:
        _update_run(store, run["id"], stage_index=run["stage_index"] + 1)
    return True


def _update_run(store, run_id: str, *, status: str | None = None,
                stage_index: int | None = None,
                error_code: str | None = None) -> None:
    with store.connection() as db:
        db.execute(text(
            "UPDATE pipeline_runs SET status=COALESCE(:status,status), "
            "stage_index=COALESCE(:step,stage_index), "
            "error_code=COALESCE(:error,error_code),updated_at=:at WHERE id=:id"
        ), {"status": status, "step": stage_index, "error": error_code,
            "at": now(), "id": run_id})
