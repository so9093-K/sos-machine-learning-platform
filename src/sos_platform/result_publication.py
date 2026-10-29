"""결과 유형별 불변 자산 게시. 호출자가 소유한 Job 트랜잭션만 사용한다."""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Connection, text

from .store_administration import now

if TYPE_CHECKING:
    from .store import Store


def publish_dataset(
    store: "Store",
    db: Connection,
    row: Mapping,
    job_id: str,
    attempt_id: str,
    result: dict | None,
    publication: dict,
    write_id: str | None,
) -> dict:
    """Dataset 참조와 객체·Snapshot 보존을 확정한다."""
    item = dict(
        id=publication.get("retention_id") or str(uuid4()),
        name=publication["name"],
        kind=publication.get("kind", "file"),
        location=publication["location"],
        version=publication["version"],
        created_at=now(),
        owner_actor=row["owner_actor"],
        source_job_id=job_id,
    )
    db.execute(
        text(
            "INSERT INTO datasets(id,name,kind,location,version,created_at,owner_actor,source_job_id) "
            "VALUES(:id,:name,:kind,:location,:version,:created_at,:owner_actor,:source_job_id)"
        ),
        item,
    )
    if write_id:
        store._publish_object(db, write_id, publication["location"], publication["version"])
    if publication.get("retention_id"):
        store._confirm_snapshot_retention(
            db, publication["retention_id"], publication["location"], publication["version"]
        )
    result = {**(result or {}), "dataset_id": item["id"]}
    store._audit(
        db,
        actor_id="sos-worker",
        actor_type="service",
        action="dataset.publish",
        resource_type="dataset",
        resource_id=item["id"],
        reason=f"job={job_id};attempt={attempt_id}",
        request_id=row["request_id"],
    )
    return result


def publish_model(
    store: "Store",
    db: Connection,
    row: Mapping,
    job_id: str,
    attempt_id: str,
    result: dict | None,
    publication: dict,
    write_id: str | None,
) -> dict:
    """Model Revision과 고정 Feature 출처·객체 참조를 확정한다."""
    if not write_id:
        raise ValueError("Missing artifact write intent")
    runtime = dict(publication["runtime"])
    # 조회 결과 Dataset의 원본 등록·조회 Job은 SOS가 이미 보존한다.
    # MLflow/Feast Registry를 복제하지 않고 Model Revision에 불변 출처만 연결한다.
    origin_id = db.execute(
        text("SELECT source_job_id FROM datasets WHERE id=:id"),
        {"id": row["dataset_id"]},
    ).scalar_one_or_none()
    feature_origin = (
        db.execute(
            text(
                "SELECT id,result FROM jobs "
                "WHERE id=:id AND kind='feature_retrieve' AND status='Succeeded'"
            ),
            {"id": origin_id},
        )
        .mappings()
        .first()
        if origin_id
        else None
    )
    if feature_origin:
        provenance = json.loads(feature_origin["result"])
        runtime["feature_retrieval"] = {
            "retrieval_job_id": feature_origin["id"],
            "registration_job_id": provenance["registration_job_id"],
            "feature_service": provenance["feature_service"],
            "feature_view": provenance["feature_view"],
            "definition_digest": provenance["definition_digest"],
            "source_dataset_id": provenance["feature_dataset_id"],
            "source_dataset_version": provenance["feature_dataset_version"],
        }
    item = {
        "id": str(uuid4()),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "dataset_id": row["dataset_id"],
        "dataset_version": publication["dataset_version"],
        "algorithm": publication["algorithm"],
        "registry_key": (json.loads(row["spec"]) or {}).get("registry_key", "default"),
        "location": publication["location"],
        "sha256": publication["sha256"],
        "features": json.dumps(publication["features"], allow_nan=False),
        "params": json.dumps(publication["params"], allow_nan=False),
        "training_score_summary": json.dumps(
            publication["training_score_summary"], allow_nan=False
        ),
        "runtime": json.dumps(runtime, allow_nan=False),
        "owner_actor": row["owner_actor"],
        "created_at": now(),
    }
    db.execute(
        text(
            "INSERT INTO model_revisions "
            "(id,job_id,attempt_id,dataset_id,dataset_version,algorithm,"
            "location,sha256,features,params,training_score_summary,runtime,"
            "registry_key,owner_actor,created_at) "
            "VALUES (:id,:job_id,:attempt_id,:dataset_id,:dataset_version,"
            ":algorithm,:location,:sha256,:features,:params,"
            ":training_score_summary,:runtime,:registry_key,:owner_actor,:created_at)"
        ),
        item,
    )
    store._publish_object(db, write_id, publication["location"], publication["sha256"])
    result = {**(result or {}), "model_revision_id": item["id"]}
    store._audit(
        db,
        actor_id="sos-worker",
        actor_type="service",
        action="model.publish",
        resource_type="model",
        resource_id=item["id"],
        reason=f"job={job_id};attempt={attempt_id}",
        request_id=row["request_id"],
    )
    return result


def publish_scoring(
    store: "Store",
    db: Connection,
    row: Mapping,
    job_id: str,
    attempt_id: str,
    result: dict | None,
    publication: dict,
    write_id: str | None,
) -> dict:
    """Scoring 결과와 점수 객체 참조를 확정한다."""
    if not write_id:
        raise ValueError("Missing artifact write intent")
    item = {
        "id": str(uuid4()),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "model_revision_id": publication["model_revision_id"],
        "dataset_id": row["dataset_id"],
        "dataset_version": publication["dataset_version"],
        "location": publication["location"],
        "sha256": publication["sha256"],
        "row_count": publication["row_count"],
        "score_direction": publication["score_direction"],
        "feature_names": json.dumps(publication["feature_names"]),
        "score_summary": json.dumps(publication["score_summary"], allow_nan=False),
        "ranking_metrics": json.dumps(publication["ranking_metrics"], allow_nan=False)
        if publication["ranking_metrics"] is not None
        else None,
        "label_column": publication["label_column"],
        "owner_actor": row["owner_actor"],
        "created_at": now(),
    }
    db.execute(
        text(
            "INSERT INTO scoring_results "
            "(id,job_id,attempt_id,model_revision_id,dataset_id,dataset_version,"
            "location,sha256,row_count,score_direction,feature_names,score_summary,"
            "ranking_metrics,label_column,owner_actor,created_at) "
            "VALUES (:id,:job_id,:attempt_id,:model_revision_id,:dataset_id,"
            ":dataset_version,:location,:sha256,:row_count,:score_direction,"
            ":feature_names,:score_summary,:ranking_metrics,:label_column,"
            ":owner_actor,:created_at)"
        ),
        item,
    )
    store._publish_object(db, write_id, publication["location"], publication["sha256"])
    result = {**(result or {}), "scoring_result_id": item["id"]}
    store._audit(
        db,
        actor_id="sos-worker",
        actor_type="service",
        action="scoring.publish",
        resource_type="scoring_result",
        resource_id=item["id"],
        reason=f"job={job_id};attempt={attempt_id}",
        request_id=row["request_id"],
    )
    return result


def publish_policy(
    store: "Store",
    db: Connection,
    row: Mapping,
    job_id: str,
    attempt_id: str,
    result: dict | None,
    publication: dict,
    write_id: str | None,
) -> dict:
    """기존 점수에 연결된 판정 정책 판본을 확정한다."""
    item = {
        "id": str(uuid4()),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "scoring_result_id": publication["scoring_result_id"],
        "mode": publication["mode"],
        "threshold": publication["threshold"],
        "alert_fraction": publication["alert_fraction"],
        "decision_summary": json.dumps(publication["decision_summary"], allow_nan=False),
        "owner_actor": row["owner_actor"],
        "created_at": now(),
    }
    db.execute(
        text(
            "INSERT INTO decision_policy_revisions "
            "(id,job_id,attempt_id,scoring_result_id,mode,threshold,alert_fraction,"
            "decision_summary,owner_actor,created_at) VALUES "
            "(:id,:job_id,:attempt_id,:scoring_result_id,:mode,:threshold,"
            ":alert_fraction,:decision_summary,:owner_actor,:created_at)"
        ),
        item,
    )
    result = {**(result or {}), "policy_revision_id": item["id"]}
    store._audit(
        db,
        actor_id="sos-worker",
        actor_type="service",
        action="policy.publish",
        resource_type="decision_policy",
        resource_id=item["id"],
        reason=f"job={job_id};attempt={attempt_id}",
        request_id=row["request_id"],
    )
    return result


def publish_evaluation(
    store: "Store",
    db: Connection,
    row: Mapping,
    job_id: str,
    attempt_id: str,
    result: dict | None,
    publication: dict,
    write_id: str | None,
) -> dict:
    """점수·정책에 연결된 평가 판본을 확정한다."""
    item = {
        "id": str(uuid4()),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "scoring_result_id": publication["scoring_result_id"],
        "policy_revision_id": publication["policy_revision_id"],
        "label_coverage": publication["label_coverage"],
        "decision_metrics": json.dumps(publication["decision_metrics"], allow_nan=False),
        "ranking_metrics": json.dumps(publication["ranking_metrics"], allow_nan=False)
        if publication["ranking_metrics"] is not None
        else None,
        "owner_actor": row["owner_actor"],
        "created_at": now(),
    }
    db.execute(
        text(
            "INSERT INTO evaluation_revisions "
            "(id,job_id,attempt_id,scoring_result_id,policy_revision_id,"
            "label_coverage,decision_metrics,ranking_metrics,owner_actor,created_at) "
            "VALUES (:id,:job_id,:attempt_id,:scoring_result_id,:policy_revision_id,"
            ":label_coverage,:decision_metrics,:ranking_metrics,:owner_actor,:created_at)"
        ),
        item,
    )
    result = {**(result or {}), "evaluation_revision_id": item["id"]}
    store._audit(
        db,
        actor_id="sos-worker",
        actor_type="service",
        action="evaluation.publish",
        resource_type="evaluation",
        resource_id=item["id"],
        reason=f"job={job_id};attempt={attempt_id}",
        request_id=row["request_id"],
    )
    return result


def publish_explanation(
    store: "Store",
    db: Connection,
    row: Mapping,
    job_id: str,
    attempt_id: str,
    result: dict | None,
    publication: dict,
    write_id: str | None,
) -> dict:
    """점수 또는 Playground에 연결된 설명 결과를 확정한다."""
    item = {
        "id": str(uuid4()),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "scoring_result_id": publication.get("scoring_result_id"),
        "playground_job_id": publication.get("playground_job_id"),
        "model_revision_id": publication["model_revision_id"],
        "row_index": publication.get("row_index"),
        "method": publication["method"],
        "output": json.dumps(publication, allow_nan=False),
        "owner_actor": row["owner_actor"],
        "created_at": now(),
    }
    db.execute(
        text(
            "INSERT INTO explanation_results "
            "(id,job_id,attempt_id,scoring_result_id,playground_job_id,"
            "model_revision_id,row_index,method,output,owner_actor,created_at) "
            "VALUES (:id,:job_id,:attempt_id,:scoring_result_id,:playground_job_id,"
            ":model_revision_id,:row_index,:method,:output,:owner_actor,:created_at)"
        ),
        item,
    )
    result = {**(result or {}), "explanation_result_id": item["id"]}
    store._audit(
        db,
        actor_id="sos-worker",
        actor_type="service",
        action="explanation.publish",
        resource_type="explanation",
        resource_id=item["id"],
        reason=f"job={job_id};attempt={attempt_id}",
        request_id=row["request_id"],
    )
    return result


def publish_profile(
    db: Connection,
    row: Mapping,
    job_id: str,
    result: dict | None,
) -> None:
    """Understanding이 남긴 부분 근거도 원래 Dataset 판본에 연결한다."""
    if (
        row["kind"] == "profile"
        and json.loads(row["spec"]).get("mode") == "understanding"
        and result is not None
    ):
        db.execute(
            text(
                "INSERT INTO dataset_profiles"
                "(job_id,dataset_id,dataset_version,scope,result,created_at) "
                "VALUES(:job,:dataset,:version,:scope,:result,:at)"
            ),
            {
                "job": job_id,
                "dataset": row["dataset_id"],
                "version": result["version"],
                "scope": result["scope"],
                "result": json.dumps(result, ensure_ascii=False, allow_nan=False),
                "at": now(),
            },
        )
