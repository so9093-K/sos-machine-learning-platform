"""모델 파일 영구 삭제. Model Revision은 결과 계보의 tombstone으로 보존한다.

MLflow 등록은 SOS 판본의 외부 기록이다(ADR 0025). 등록 전송을 한 번도 시도하지 않은 판본은 SOS
자산만 삭제한다. 등록했거나 등록 결과가 불확실한 판본은 MLflow의 실제 버전을 확인해 함께 삭제한다.
전송 중인 판본은 등록과 삭제가 엇갈리지 않도록 전송이 끝난 뒤 삭제한다.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import text

from .mlflow_errors import MLflowDeliveryError
from .model_artifacts import revision_artifact_key
from .mlflow_tracking import MLflowClient
from .mlflow_registry import delete_registered_versions
from .mlflow_config import MLflowSettings
from .errors import RuntimeConfigurationError
from .object_store import ObjectStore
from .security import Actor, require
from .store import Store

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
            (SELECT count(*) FROM explanation_results WHERE model_revision_id=:id) AS explanations
        """), {"id": model_id}).mappings().one()
        return {**{key:int(value) for key,value in counts.items()},
                **deletion_impact_transaction(db, model_id)}


def begin_delete(store: Store, model_id: str, actor: Actor) -> tuple[dict, bool]:
    """새 모델 사용을 차단하고, 외부 삭제 재시도가 가능한 상태를 확정한다.

    Returns:
        판본 행과 MLflow 등록 전송을 시도한 적이 있는지 여부.
    """
    with store.connection() as db:
        row = db.execute(
            text("SELECT * FROM model_revisions WHERE id=:id FOR UPDATE"), {"id":model_id}
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")
        if not actor.privileged and row["owner_actor"]!=actor.id:
            raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")
        # 전송 행을 잠가 Worker가 이 판본의 등록 전송을 동시에 차지하지 못하게 한다.
        export = db.execute(text(
            "SELECT state,attempts FROM mlflow_exports WHERE job_id=:job FOR UPDATE"
        ), {"job": row["job_id"]}).mappings().first()
        attempted = bool(row["registry_version"]) or bool(export and export["attempts"])
        if row["lifecycle_state"] == "Deleted":
            return dict(row), attempted
        # 제공·중단도 같은 모델 행을 잠근다. 제공 확인과 삭제 전환 사이에
        # 새 제공이 끼어들지 못하며, 외부 삭제 재시도 전에도 중단을 요구한다.
        offered = db.execute(text(
            "SELECT 1 FROM model_offerings WHERE model_revision_id=:id "
            "AND withdrawn_at IS NULL"
        ), {"id": model_id}).scalar_one_or_none()
        if offered:
            raise HTTPException(status_code=409, detail="제공 중인 모델입니다. 먼저 모델 제공을 중단하세요.")
        if row["lifecycle_state"] == "Active":
            if export and export["state"] == "Sending":
                raise HTTPException(status_code=409, detail="MLflow 등록 기록을 전송하고 있습니다. "
                                    "잠시 후 다시 요청하세요.")
            # 단일 트랜잭션의 행 잠금은 새 사용 요청과 삭제 경합 시 직렬화에 사용한다.
            current = deletion_impact_transaction(db, model_id)
            if current["active_jobs"] or current["shared_artifacts"] or current["serving_endpoints"]:
                raise HTTPException(status_code=409, detail={
                    "message":"실행 중인 작업, 공유 중인 모델 파일 또는 이 모델을 쓰는 서빙 Endpoint가 있어 "
                              "삭제할 수 없습니다.",
                    "impact":current,
                })
            db.execute(text(
                "UPDATE model_revisions SET lifecycle_state='Deleting' "
                "WHERE id=:id AND lifecycle_state='Active'"
            ), {"id":model_id})
            store._audit(db,actor_id=actor.id,actor_type=actor.kind,
                action="model.delete_requested",resource_type="model",resource_id=model_id)
        return dict(row), attempted


def deletion_impact_transaction(db, model_id: str) -> dict:
    """삭제를 막는 참조. 영향 조회와 삭제 트랜잭션이 같은 판정을 쓴다.

    활성 Job은 모델 파일을 읽는 모든 접수 경로를 포함한다. 모델을 직접 지정한 Job,
    Scoring 결과를 거치는 Job, Playground 결과를 거치는 설명 Job이다. 서빙 Endpoint가 활성·
    전환 대상·롤백 대상으로 참조하는 판본은 상주 복제본이 아티팩트를 쓰거나 다시 쓸 수 있다.
    """
    record=db.execute(text("""
        SELECT
          (SELECT count(*) FROM jobs WHERE status IN ('Queued','Running')
            AND ((spec::jsonb->>'model_revision_id')=:id OR
              (spec::jsonb->>'scoring_result_id') IN
                (SELECT id FROM scoring_results WHERE model_revision_id=:id) OR
              (spec::jsonb->>'playground_job_id') IN
                (SELECT id FROM jobs WHERE kind='ml_playground'
                  AND (spec::jsonb->>'model_revision_id')=:id))) AS active_jobs,
          (SELECT count(*) FROM model_revisions WHERE id!=:id AND location=
            (SELECT location FROM model_revisions WHERE id=:id)
            AND lifecycle_state!='Deleted') AS shared_artifacts,
          (SELECT count(*) FROM serving_endpoints WHERE :id IN (active_model_revision_id,
            pending_model_revision_id, previous_model_revision_id)) AS serving_endpoints
    """),{"id":model_id}).mappings().one()
    return {key:int(value) for key,value in record.items()}


def finish_delete(store: Store, model_id: str, actor: Actor) -> None:
    with store.connection() as db:
        updated = db.execute(text(
            "UPDATE model_revisions SET lifecycle_state='Deleted' "
            "WHERE id=:id AND lifecycle_state='Deleting'"
        ), {"id":model_id})
        if updated.rowcount:
            store._audit(db,actor_id=actor.id,actor_type=actor.kind,
                action="model.permanently_deleted",resource_type="model",resource_id=model_id)


def model_deletion_router(store: Store) -> APIRouter:
    router=APIRouter()

    @router.get("/api/ml/models/deletion-pending",tags=["Models"])
    def pending_deletions(actor:Actor=READ):
        """Registry에서 사라졌더라도 실제 삭제가 끝나지 않은 모델을 재시도할 수 있게 한다."""
        with store.connection() as db:
            if actor.privileged:
                rows = db.execute(text(
                    "SELECT id,registry_key,registry_version,created_at FROM model_revisions "
                    "WHERE lifecycle_state='Deleting' ORDER BY created_at DESC LIMIT 100"
                )).mappings().all()
            else:
                rows = db.execute(text(
                    "SELECT id,registry_key,registry_version,created_at FROM model_revisions "
                    "WHERE lifecycle_state='Deleting' AND owner_actor=:actor "
                    "ORDER BY created_at DESC LIMIT 100"
                ), {"actor":actor.id}).mappings().all()
        return {"items":[dict(item) for item in rows]}

    @router.get("/api/ml/models/{revision_id}/deletion-impact",tags=["Models"])
    def impact(revision_id: str,actor:Actor=READ):
        item=store.get_model_revision(revision_id)
        if item is None or (not actor.privileged and item["owner_actor"]!=actor.id):
            raise HTTPException(status_code=404,detail="모델을 찾을 수 없습니다.")
        return {"model_id":revision_id,"lifecycle_state":item["lifecycle_state"],
                "references":deletion_impact(store,revision_id)}

    @router.delete("/api/ml/models/{revision_id}",tags=["Models"])
    def permanently_delete_model(
        revision_id:str,confirm:bool=Query(default=False),actor:Actor=MANAGE,
    ):
        if not confirm:
            raise HTTPException(status_code=422,detail="모델 삭제에는 명시적 확인이 필요합니다.")
        # 설정 오류로 삭제 의도만 Deleting에 남기지 않도록 먼저 확인한다.
        try:
            settings = MLflowSettings.from_env()
        except RuntimeConfigurationError as exc:
            raise HTTPException(status_code=503, detail="MLflow 연결 설정을 확인하세요.") from exc
        row, attempted = begin_delete(store,revision_id,actor)
        if row["lifecycle_state"]=="Deleted":
            return {"model_id":revision_id,"state":"Deleted"}
        # SOS는 실제 Registry와 S3 객체가 삭제되기 전까지 Deleting을 유지한다.
        try:
            # 대상 객체와 게시 Attempt가 일치하는지 외부 Registry 변경보다 먼저 검증한다.
            storage=ObjectStore()
            key=storage.key(row["location"])
            # 아티팩트 형식은 판본 Runtime 기록이 정한다(SOS 학습 joblib·MLflow 연결 skops).
            if key != revision_artifact_key({**row, "runtime": json.loads(row["runtime"])}):
                raise ValueError("Model artifact does not match its publishing Attempt")
            if attempted:
                # 등록했거나 등록 결과가 불확실하면 MLflow의 실제 버전을 확인해 지운다.
                if settings is None:
                    raise RuntimeError("MLflow registration exists but MLflow is not configured")
                delete_registered_versions(MLflowClient(settings), row)
            storage.permanently_delete(key)
        except (MLflowDeliveryError, ValueError, OSError, RuntimeError, BotoCoreError, ClientError) as exc:
            # 외부 시스템은 공통 트랜잭션이 아니므로 중단된 상태를 실패로 확정하지 않는다.
            # 같은 요청을 다시 보내 삭제를 재개한다. 비밀 정보는 응답에 노출하지 않는다.
            raise HTTPException(status_code=503, detail="모델 삭제가 완료되지 않았습니다. 상태를 확인한 뒤 다시 요청하세요.") from exc
        finish_delete(store,revision_id,actor)
        return {"model_id":revision_id,"state":"Deleted"}

    return router
