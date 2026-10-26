"""모델 파일 영구 삭제. Model Revision은 결과 계보의 tombstone으로 보존한다."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from .mlflow_errors import MLflowDeliveryError
from .mlflow_tracking import MLflowClient
from .mlflow_config import MLflowSettings
from .object_store import ObjectStore
from .security import Actor, require
from .store import Store
from .store_administration import now

READ = Depends(require("job.read"))
MANAGE = Depends(require("model.manage"))


def deletion_impact(store: Store, model_id: str) -> dict:
    """실제 FK와 실행 요청을 기준으로 영향 범위를 조회한다."""
    with store.connection() as db:
        counts = db.execute(text("""
            SELECT
            (SELECT count(*) FROM scoring_results WHERE model_revision_id=:id) AS scoring,
            (SELECT count(*) FROM decision_policy_revisions p
                JOIN scoring_results s ON s.id=p.scoring_result_id
                WHERE s.model_revision_id=:id) AS policies,
            (SELECT count(*) FROM evaluation_revisions e
                JOIN scoring_results s ON s.id=e.scoring_result_id
                WHERE s.model_revision_id=:id) AS evaluations,
            (SELECT count(*) FROM explanation_results WHERE model_revision_id=:id) AS explanations,
            (SELECT count(*) FROM jobs WHERE status IN ('Queued','Running')
                AND ((spec::jsonb->>'model_revision_id')=:id OR
                  (spec::jsonb->>'scoring_result_id') IN
                    (SELECT id FROM scoring_results WHERE model_revision_id=:id))) AS active_jobs,
            (SELECT count(*) FROM model_revisions WHERE id!=:id AND location=
                (SELECT location FROM model_revisions WHERE id=:id)
                AND lifecycle_state!='Deleted') AS shared_artifacts
        """), {"id": model_id}).mappings().one()
    return {key:int(value) for key,value in counts.items()}


def begin_delete(store: Store, model_id: str, actor: Actor) -> dict:
    """새 모델 사용을 차단하고, 외부 삭제 재시도가 가능한 상태를 확정한다."""
    with store.connection() as db:
        row = db.execute(
            text("SELECT * FROM model_revisions WHERE id=:id FOR UPDATE"), {"id":model_id}
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Model not found")
        if not actor.privileged and row["owner_actor"]!=actor.id:
            raise HTTPException(status_code=404, detail="Model not found")
        if row["lifecycle_state"] == "Deleted":
            return dict(row)
        if row["lifecycle_state"] == "Active":
            tracking = db.execute(text(
                "SELECT state FROM mlflow_exports WHERE job_id=:id"
            ), {"id":row["job_id"]}).scalar_one_or_none()
            if tracking != "Succeeded" or not row["registry_version"]:
                raise HTTPException(status_code=409, detail="Registry publication has not completed")
            # 단일 트랜잭션의 행 잠금은 새 사용 요청과 삭제 경합 시 직렬화에 사용한다.
            current = deletion_impact_transaction(db, model_id)
            if current["active_jobs"] or current["shared_artifacts"]:
                raise HTTPException(status_code=409, detail={
                    "message":"Active jobs or shared model artifacts prevent deletion",
                    "impact":current,
                })
            db.execute(text(
                "UPDATE model_revisions SET lifecycle_state='Deleting' "
                "WHERE id=:id AND lifecycle_state='Active'"
            ), {"id":model_id})
            store._audit(db,actor_id=actor.id,actor_type=actor.kind,
                action="model.delete_requested",resource_type="model",resource_id=model_id)
        return dict(row)


def deletion_impact_transaction(db, model_id: str) -> dict:
    """삭제 트랜잭션 안에서 참조를 재확인한다."""
    record=db.execute(text("""
        SELECT
          (SELECT count(*) FROM jobs WHERE status IN ('Queued','Running')
            AND ((spec::jsonb->>'model_revision_id')=:id OR
              (spec::jsonb->>'scoring_result_id') IN
                (SELECT id FROM scoring_results WHERE model_revision_id=:id))) AS active_jobs,
          (SELECT count(*) FROM model_revisions WHERE id!=:id AND location=
            (SELECT location FROM model_revisions WHERE id=:id)
            AND lifecycle_state!='Deleted') AS shared_artifacts
    """),{"id":model_id}).mappings().one()
    return {key:int(value) for key,value in record.items()}


def finish_delete(store: Store, model_id: str, actor: Actor) -> None:
    with store.connection() as db:
        db.execute(text(
            "UPDATE model_revisions SET lifecycle_state='Deleted' "
            "WHERE id=:id AND lifecycle_state='Deleting'"
        ), {"id":model_id})
        store._audit(db,actor_id=actor.id,actor_type=actor.kind,
            action="model.permanently_deleted",resource_type="model",resource_id=model_id)


def model_deletion_router(store: Store) -> APIRouter:
    router=APIRouter()

    @router.get("/api/ml/models/{revision_id}/deletion-impact",tags=["Models"])
    def impact(revision_id: str,actor:Actor=READ):
        item=store.get_model_revision(revision_id)
        if item is None or (not actor.privileged and item["owner_actor"]!=actor.id):
            raise HTTPException(status_code=404,detail="Model not found")
        return {"model_id":revision_id,"lifecycle_state":item["lifecycle_state"],
                "references":deletion_impact(store,revision_id)}

    @router.delete("/api/ml/models/{revision_id}",tags=["Models"])
    def permanently_delete_model(
        revision_id:str,confirm:bool=Query(default=False),actor:Actor=MANAGE,
    ):
        if not confirm:
            raise HTTPException(status_code=422,detail="Deletion requires explicit confirmation")
        row=begin_delete(store,revision_id,actor)
        if row["lifecycle_state"]=="Deleted":
            return {"model_id":revision_id,"state":"Deleted"}
        # SOS는 실제 Registry와 S3 객체가 삭제되기 전까지 Deleting을 유지한다.
        try:
            client=MLflowClient(MLflowSettings.from_env())
            try:
                client.post("model-versions/delete", {
                    "name":row["registry_model"],"version":row["registry_version"],
                })
            except MLflowDeliveryError as exc:
                if not str(exc).startswith("SOS-MLFLOW-HTTP-404"):
                    raise
            storage=ObjectStore()
            key=storage.key(row["location"])
            if not key.startswith("results/") or not key.endswith(".model.joblib"):
                raise ValueError("Only SOS-published model artifacts may be deleted")
            storage.permanently_delete(key)
        except (MLflowDeliveryError, ValueError, OSError, Exception) as exc:
            # 외부 시스템은 공통 트랜잭션이 아니므로 중단된 상태를 실패로 확정하지 않는다.
            # 같은 요청을 다시 보내 삭제를 재개한다. 비밀 정보는 응답에 노출하지 않는다.
            raise HTTPException(status_code=503, detail="Model deletion incomplete; retry") from exc
        finish_delete(store,revision_id,actor)
        return {"model_id":revision_id,"state":"Deleted"}

    return router
