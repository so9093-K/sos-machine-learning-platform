"""API 라우터가 공유하는 리소스 접근 확인."""

import logging

from fastapi import HTTPException

from .errors import PlatformError
from .observability import emit
from .security import Actor, resource_allowed
from .store import Store


class ResourceAccess:
    """Actor의 리소스 소유권·모델 제공 범위·제출 키 정책을 한곳에서 판정한다.

    모든 거부는 존재 여부를 숨기는 404이며, 소유권 거부는 감사 기록에 남긴다.
    """

    def __init__(self, store: Store, identity_mode: str) -> None:
        self._store = store
        self._identity_mode = identity_mode

    def check_ownership(self, actor: Actor, owner: str | None, kind: str, identifier: str) -> None:
        """소유자·관리자가 아니면 404로 거부하고 거부 사실을 감사한다."""
        try:
            resource_allowed(actor, owner)
        except HTTPException:
            try:
                self._store.record_audit(
                    actor_id=actor.id,
                    actor_type=actor.kind,
                    action="security.resource_access_denied",
                    resource_type=kind,
                    resource_id=identifier,
                    decision="deny",
                    outcome="denied",
                    reason="ownership",
                )
            except Exception:
                emit(
                    "audit.resource_denial.record_failed",
                    service="sos-api",
                    level=logging.ERROR,
                    error_code="SOS-API-INTERNAL",
                )
            raise

    def dataset(self, dataset_id: str, actor: Actor) -> dict:
        item = self._store.get_dataset(dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset을 찾을 수 없습니다.")
        self.check_ownership(actor, item.get("owner_actor"), "dataset", dataset_id)
        return item

    def job(self, job_id: str, actor: Actor) -> dict:
        item = self._store.get_job(job_id)
        if not item:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        self.check_ownership(actor, item.get("owner_actor"), "job", job_id)
        return item

    def require_submission_key(self, value: str | None) -> None:
        """외부 토큰 API에서 무키 Job 재전송을 기본 거부한다."""
        if self._identity_mode == "token" and value is None:
            raise PlatformError("SOS-JOB-IDEMPOTENCY-REQUIRED")

    def model(self, revision_id: str) -> dict:
        """권한 판정 전의 모델 판본 조회. 응답에 직접 쓰지 않는다."""
        item = self._store.get_model_revision(revision_id)
        if item is None:
            raise HTTPException(status_code=404, detail="모델 판본을 찾을 수 없습니다.")
        return item

    def model_allowed(self, actor: Actor, model: dict, *, for_new_work: bool) -> dict:
        """소유자·관리자가 아니면 소유자가 제공한 판본만 허용한다.

        제공이 중단된 판본은 계속 읽을 수 있지만 새 계산에는 사용할 수 없다.
        """
        if not actor.privileged and model.get("owner_actor") != actor.id:
            offering = self._store.get_model_offering(model["id"])
            if offering is not None and (offering["withdrawn_at"] is None or not for_new_work):
                return model
        self.check_ownership(actor, model.get("owner_actor"), "model", model["id"])
        return model

    def readable_model(self, revision_id: str, actor: Actor) -> dict:
        return self.model_allowed(actor, self.model(revision_id), for_new_work=False)

    def model_response(self, model: dict, actor: Actor) -> dict:
        """조회 응답만 Actor에 맞게 투영한다. Worker·추론의 원본 판본은 변경하지 않는다."""
        visible = actor.privileged or model.get("owner_actor") == actor.id
        response = {**model, "creation_evidence_visible": visible}
        if visible:
            return response
        runtime = model["runtime"]
        # 소비자에게 필요한 입력·출력·실행 호환 정보만 허용한다. 내부 선택 정책은 포함하지 않는다.
        public_runtime = {key: runtime[key] for key in (
            "model_format", "artifact_schema", "loading", "output", "feature_dtypes",
            "feature_reference_medians", "python", "numpy", "scikit_learn", "polars",
        ) if key in runtime}
        profile = runtime.get("feature_training_profile")
        if isinstance(profile, dict):
            public_profile = {key: profile[key] for key in ("method", "input_space") if key in profile}
            ranges = profile.get("features")
            if isinstance(ranges, dict):
                public_profile["features"] = {
                    name: {key: ranges[name][key] for key in ("min", "p10", "p50", "p90", "max")
                           if key in ranges[name]}
                    for name in model["features"] if isinstance(ranges.get(name), dict)
                }
            public_runtime["feature_training_profile"] = public_profile
        response["runtime"] = public_runtime
        # Histogram의 건수를 합해도 학습 행 수를 알 수 있으므로 요약 전체를 공급자 전용으로 둔다.
        response["training_score_summary"] = {}
        return response

    def owned_model(self, revision_id: str, actor: Actor) -> dict:
        item = self.model(revision_id)
        self.check_ownership(actor, item.get("owner_actor"), "model", item["id"])
        return item
