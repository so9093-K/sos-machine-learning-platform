"""모델 적용(Scoring·단일 입력)과 판정 정책·평가·설명 API."""

import math

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .api_access import ResourceAccess
from .discovery import columns as discovery_columns
from .ml_input import model_input_issues, ordered_playground_input
from .ml_policy_contract import validate_policy_for_scoring
from .pagination import decode_cursor, page_from_rows
from .schemas import (
    DecisionPolicyRevisionOut,
    EvaluationRevisionOut,
    ExplanationResultOut,
    JobOut,
    MLEvaluationRequest,
    MLExplanationRequest,
    MLPlaygroundRequest,
    MLPolicyRequest,
    MLScoringRequest,
    ModelEvaluationPageOut,
    ScoringResultOut,
)
from .security import Actor, require
from .store import Store

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
READ_JOB_DEP = Depends(require("job.read"))
CREATE_JOB_DEP = Depends(require("job.create"))


def ml_results_router(store: Store, access: ResourceAccess) -> APIRouter:
    """모델 적용 결과와 그 위의 독립 결과 판본을 제출·조회한다."""
    router = APIRouter()

    @router.post(
        "/api/ml/playground/jobs",
        tags=["Scoring"],
        summary="단일 관측치 모델 점수화 작업 제출",
        description="Dataset 등록 없이 입력값을 Worker에서 계산합니다. 입력 Feature는 Job 이력에 보존됩니다.",
        status_code=202,
        response_model=JobOut,
    )
    def create_playground_job(
        body: MLPlaygroundRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", min_length=8, max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        # 읽기 권한만 확인한다. 현재 제공 여부·판본 상태는 Store가 새 Job을 만들 때 판정하므로, 같은 키
        # 재요청은 제공 중단 뒤에도 원래 Job을 받는다.
        model = access.model_allowed(actor, access.model(body.model_revision_id), for_new_work=False)
        try:
            ordered_playground_input(model, body.features)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_playground", dataset_id=None,
            spec={
                "model_revision_id": model["id"],
                "model_owner_actor": model["owner_actor"],
                "features": body.features,
            },
            request_id=request.state.request_id,
            owner_actor=actor.id, actor_type=actor.kind,
            idempotency_key=idempotency_key,
            require_model_offering=not actor.privileged and model["owner_actor"] != actor.id,
        )

    @router.post(
        "/api/ml/scoring/jobs",
        tags=["Scoring"],
        summary="모델 점수화 작업 제출",
        description="확정된 모델과 Dataset을 사용해 별도의 배치 Scoring Job을 접수합니다. 학습과 독립적인 실행입니다.",
        status_code=202,
        response_model=JobOut,
    )
    def create_scoring_job(
        body: MLScoringRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        dataset = access.dataset(body.dataset_id, actor)
        # 읽기 권한만 확인한다. 현재 제공 여부·판본 상태는 Store가 새 Job을 만들 때 판정한다.
        model = access.model_allowed(actor, access.model(body.model_revision_id), for_new_work=False)
        access.require_submission_key(idempotency_key)
        # 고정 판본의 입력 구조 불일치는 Worker 실패 전에 원인 컬럼과 함께 거부한다.
        input_issues = model_input_issues(
            model["features"], model["runtime"].get("feature_dtypes"),
            {column["name"]: column["dtype"] for column in discovery_columns(store, dataset)["columns"]},
        )
        if input_issues:
            raise HTTPException(status_code=422, detail=" · ".join(input_issues))
        return store.create_job(
            kind="ml_score",
            dataset_id=dataset["id"],
            spec={
                "model_revision_id": model["id"],
                "model_owner_actor": model["owner_actor"],
                "dataset_version": dataset["version"],
                "label_column": body.label_column,
                "observation_id_column": body.observation_id_column,
                "observation_time_column": body.observation_time_column,
                "temporal_scope": (
                    body.temporal_scope.model_dump(mode="json")
                    if body.temporal_scope is not None else None
                ),
            },
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
            idempotency_key=idempotency_key,
            require_model_offering=not actor.privileged and model["owner_actor"] != actor.id,
        )

    def existing_scoring_job(job_id: str, actor: Actor) -> dict:
        item = access.job(job_id, actor)
        if item["kind"] != "ml_score":
            raise HTTPException(status_code=404, detail="점수 계산 작업을 찾을 수 없습니다.")
        return item

    def required_scoring_result(result_id: str, actor: Actor) -> dict:
        scoring = store.get_scoring_result(result_id)
        if scoring is None:
            raise HTTPException(status_code=404, detail="점수 결과를 찾을 수 없습니다.")
        access.check_ownership(actor, scoring["owner_actor"], "scoring_result", result_id)
        return scoring

    @router.post(
        "/api/ml/policies/jobs",
        tags=["Decision Policies"],
        summary="판정 정책 작업 제출",
        description="Scoring 결과에 별도의 판정 정책을 생성합니다. 임계값을 바꾸기 위해 모델을 재학습할 필요는 없습니다.",
        status_code=202,
        response_model=JobOut,
    )
    def create_policy_job(
        body: MLPolicyRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        score = required_scoring_result(body.scoring_result_id, actor)
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_policy",
            dataset_id=score["dataset_id"],
            spec={
                **body.model_dump(),
                "source_owner_actor": score["owner_actor"],
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    @router.post(
        "/api/ml/evaluations/jobs",
        tags=["Evaluations"],
        summary="평가 작업 제출",
        description="Scoring·정책·레이블의 실제 근거에 따라 평가를 실행합니다. 레이블 없는 결과에 감독 지표를 만들지 않습니다.",
        status_code=202,
        response_model=JobOut,
    )
    def create_evaluation_job(
        body: MLEvaluationRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        score = required_scoring_result(body.scoring_result_id, actor)
        if body.policy_revision_id:
            policy = store.get_decision_policy_revision(body.policy_revision_id)
            if policy is None:
                raise HTTPException(status_code=404, detail="판정 정책을 찾을 수 없습니다.")
            access.check_ownership(actor, policy["owner_actor"], "decision_policy", policy["id"])
            reference = required_scoring_result(policy["scoring_result_id"], actor)
            try:
                validate_policy_for_scoring(store, policy, score)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_evaluate",
            dataset_id=score["dataset_id"],
            spec={
                **body.model_dump(),
                "source_owner_actor": score["owner_actor"],
                **({
                    "policy_owner_actor": policy["owner_actor"],
                    "reference_owner_actor": reference["owner_actor"],
                } if body.policy_revision_id else {}),
            },
            owner_actor=actor.id,
            actor_type=actor.kind,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/api/ml/policies/{revision_id}",
        tags=["Decision Policies"],
        summary="판정 정책 판본 조회",
        response_model=DecisionPolicyRevisionOut,
    )
    def get_policy_revision(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_decision_policy_revision(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="판정 정책을 찾을 수 없습니다.")
        access.check_ownership(actor, item["owner_actor"], "decision_policy", revision_id)
        return item

    @router.post(
        "/api/ml/explanations/jobs",
        tags=["Explanations"],
        summary="모델 설명 작업 제출",
        description="Scoring 결과 행 또는 성공한 단일 입력 Job을 설명합니다. Feature 민감도와 SHAP은 실제 이상 사건의 인과 원인이 아닙니다.",
        status_code=202,
        response_model=JobOut,
    )
    def create_explanation_job(
        body: MLExplanationRequest,
        request: Request,
        actor: Actor = CREATE_JOB_DEP,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ):
        if body.playground_job_id is not None:
            source = access.job(body.playground_job_id, actor)
            if source["kind"] != "ml_playground" or source["status"] != "Succeeded":
                raise HTTPException(
                    status_code=409, detail="설명하려면 성공한 단일 입력 점수 계산 작업이 필요합니다."
                )
            saved = source.get("result")
            score = saved.get("anomaly_score") if isinstance(saved, dict) else None
            if type(score) not in (float, int) or not math.isfinite(score):
                raise HTTPException(status_code=409, detail="단일 입력 점수를 사용할 수 없습니다.")
            model = store.get_model_revision(source["spec"]["model_revision_id"])
            if model is None:
                raise HTTPException(status_code=409, detail="단일 입력 계산에 사용한 모델을 사용할 수 없습니다.")
            # 읽기 권한만 확인한다. 판본 상태와 다른 Actor의 현재 제공 여부는 Store가 새 Job을 만들 때 판정한다.
            access.model_allowed(actor, model, for_new_work=False)
            require_offering = not actor.privileged and model["owner_actor"] != actor.id
            dataset_id = None
            source_spec = {
                "playground_job_id": source["id"],
                "source_owner_actor": source["owner_actor"],
                "method": body.method,
            }
        else:
            score = required_scoring_result(body.scoring_result_id, actor)
            if body.row_index >= score["row_count"]:
                raise HTTPException(status_code=422, detail="설명할 행 번호가 점수 결과의 범위를 벗어났습니다.")
            dataset_id = score["dataset_id"]
            # 자신이 접근할 수 있는 점수 결과의 설명은 모델 제공 여부와 무관하다.
            require_offering = False
            source_spec = {
                "scoring_result_id": score["id"],
                "source_owner_actor": score["owner_actor"],
                "row_index": body.row_index,
                "method": body.method,
            }
        access.require_submission_key(idempotency_key)
        return store.create_job(
            kind="ml_explain",
            dataset_id=dataset_id,
            spec=source_spec,
            request_id=request.state.request_id,
            owner_actor=actor.id,
            actor_type=actor.kind,
            idempotency_key=idempotency_key,
            require_model_offering=require_offering,
        )

    @router.get(
        "/api/ml/explanations/{revision_id}",
        tags=["Explanations"],
        summary="모델 설명 결과 조회",
        response_model=ExplanationResultOut,
    )
    def get_explanation_result(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_explanation_result(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="설명 결과를 찾을 수 없습니다.")
        access.check_ownership(actor, item["owner_actor"], "explanation", revision_id)
        return item

    @router.get(
        "/api/ml/models/{revision_id}/evaluations",
        tags=["Evaluations"],
        summary="모델 판본의 평가 근거 조회",
        description="열람 가능한 모델의 평가를 계정 소유권 범위에서 조회합니다. 관리자는 전체 평가를 조회하며 모델 제공은 공급자 평가를 공개하지 않습니다.",
        response_model=ModelEvaluationPageOut,
    )
    def model_evaluations(
        revision_id: str, limit: int = Query(default=5, ge=1, le=50),
        cursor: str | None = Query(default=None, max_length=512), actor: Actor = READ_JOB_DEP,
    ):
        access.readable_model(revision_id, actor)
        rows = store.list_model_evaluations(
            revision_id, owner_actor=None if actor.privileged else actor.id,
            limit=limit + 1, before=decode_cursor(cursor, "모델 평가"),
        )
        return page_from_rows(rows, limit)

    @router.get(
        "/api/ml/evaluations/{revision_id}",
        tags=["Evaluations"],
        summary="평가 결과 판본 조회",
        response_model=EvaluationRevisionOut,
    )
    def get_evaluation_revision(revision_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_evaluation_revision(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="평가 결과를 찾을 수 없습니다.")
        access.check_ownership(actor, item["owner_actor"], "evaluation", revision_id)
        return item

    @router.get(
        "/api/ml/scoring/results/{result_id}",
        tags=["Scoring"],
        summary="점수화 결과 조회",
        response_model=ScoringResultOut,
    )
    def get_scoring_result(result_id: str, actor: Actor = READ_JOB_DEP):
        item = store.get_scoring_result(result_id)
        if item is None:
            raise HTTPException(status_code=404, detail="점수 결과를 찾을 수 없습니다.")
        access.check_ownership(actor, item["owner_actor"], "scoring_result", result_id)
        return item

    return router
