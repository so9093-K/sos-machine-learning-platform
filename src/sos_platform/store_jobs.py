"""Job 제출·멱등성·Attempt 상태 변경과 중단 복구의 PostgreSQL 정본."""

import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import bindparam, text

from .errors import ERRORS, InvalidRequestError, PlatformError, WorkerOwnershipLost
from .store_administration import now


class IcebergCancellationConflict(Exception):
    """외부 Iceberg 커밋 완료 여부가 불명확할 때 취소를 거부한다."""


class FeatureRetirementCancellationConflict(Exception):
    """진행 중인 Feast Registry 객체 삭제는 부분 커밋을 남길 수 있어 취소할 수 없다."""


class JobStoreMixin:
    """Store의 공통 트랜잭션 경계를 통해 작업 생명주기를 관리한다."""

    def job_workflow_source(self, job: dict) -> dict | None:
        """SOS 제출·소유권 기록에서 출처를 읽는다. 오케스트레이터에 연결하지 않는다.

        단계명은 실행 권한이 아닌 출처 메타데이터이며 특정 Pipeline의 단계 목록에
        종속되지 않는다. 제출 키만 흉내 낸 다른 Actor의 Job은 연결하지 않는다.
        """
        with self.connection() as db:
            key = db.execute(text(
                "SELECT submission_key FROM job_submissions WHERE job_id=:job AND actor_id=:owner"
            ), {"job": job["id"], "owner": job.get("owner_actor")}).scalar_one_or_none()
            parts = key.split(":") if isinstance(key, str) else []
            if len(parts) != 3 or parts[0] != "workflow" or not parts[1] or not parts[2]:
                return None
            row = db.execute(text(
                "SELECT id,definition_id FROM workflow_submissions WHERE id=:id AND owner_actor=:owner"
            ), {"id": parts[1], "owner": job.get("owner_actor")}).mappings().first()
        return None if row is None else {
            "submission_id": row["id"], "stage": parts[2], "definition_id": row["definition_id"],
        }

    def create_job(
        self,
        *,
        kind: str,
        dataset_id: str | None,
        spec: dict,
        owner_actor: str | None = None,
        actor_type: str = "human",
        request_id: str | None = None,
        idempotency_key: str | None = None,
        reserve_iceberg_table: str | None = None,
        require_model_offering: bool = False,
    ) -> dict:
        """중복 제출을 PostgreSQL 유일성 제약과 같은 트랜잭션으로 처리한다."""
        if dataset_id is None and not (
            kind == "ml_playground"
            or (kind == "ml_explain" and spec.get("playground_job_id"))
        ):
            raise ValueError("Only Playground or Playground Explanation Jobs may omit a Dataset")
        if idempotency_key is not None and owner_actor is None:
            raise ValueError("Idempotent Job requires an actor")
        if reserve_iceberg_table is not None and (kind != "iceberg_import" or not owner_actor):
            raise ValueError("Iceberg table reservation requires an import actor")
        fingerprint = hashlib.sha256(
            json.dumps(
                {"kind": kind, "dataset_id": dataset_id, "spec": spec},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        item = dict(
            id=str(uuid4()),
            kind=kind,
            dataset_id=dataset_id,
            owner_actor=owner_actor,
            request_id=request_id,
            spec=json.dumps(spec),
            status="Queued",
            created_at=now(),
            updated_at=now(),
        )
        with self.connection() as db:
            if kind in ("ml_score", "ml_playground"):
                model_id = spec.get("model_revision_id")
                state = db.execute(text(
                    "SELECT lifecycle_state FROM model_revisions WHERE id=:id FOR SHARE"
                ), {"id": model_id}).scalar_one_or_none()
                if state != "Active":
                    raise PlatformError("SOS-ML-MODEL-INVALID")
                # API에서 제공 상태를 읽은 뒤 중단이 확정될 수 있다. 중단·삭제와
                # 같은 모델 행 잠금 아래에서 교차 Actor의 신규 사용을 재확인한다.
                if require_model_offering and db.execute(text(
                    "SELECT 1 FROM model_offerings WHERE model_revision_id=:id "
                    "AND withdrawn_at IS NULL"
                ), {"id": model_id}).scalar_one_or_none() is None:
                    raise PlatformError("SOS-API-CONFLICT")
            if kind == "ml_explain":
                # 설명 대상의 모델 파일이 삭제되는 상황과 계산 접수를 직렬화한다.
                if spec.get("playground_job_id"):
                    model_state = db.execute(text(
                        "SELECT m.lifecycle_state FROM model_revisions m "
                        "JOIN jobs j ON j.spec::jsonb ->> 'model_revision_id'=m.id "
                        "WHERE j.id=:job_id AND j.kind='ml_playground' "
                        "AND j.status='Succeeded' FOR SHARE OF m"
                    ), {"job_id": spec["playground_job_id"]}).scalar_one_or_none()
                else:
                    model_state = db.execute(text(
                        "SELECT m.lifecycle_state FROM model_revisions m "
                        "JOIN scoring_results s ON s.model_revision_id=m.id "
                        "WHERE s.id=:scoring_id FOR SHARE OF m"
                    ), {"scoring_id": spec.get("scoring_result_id")}).scalar_one_or_none()
                if model_state != "Active":
                    raise PlatformError("SOS-ML-MODEL-INVALID")
            is_understanding = (
                kind == "profile"
                and spec.get("mode") == "understanding"
                and owner_actor is not None
            )
            if is_understanding:
                # 같은 Actor의 여러 탭이 동시에 새 프로파일을 만들지 않게 직렬화한다.
                # 다른 Actor에게 기존 Job과 요청 메타데이터를 노출하지 않는다.
                db.execute(
                    text("SELECT id FROM datasets WHERE id=:id FOR UPDATE"),
                    {"id": dataset_id},
                ).scalar_one()
            if idempotency_key is not None:
                # PostgreSQL 유일성 충돌은 동시 트랜잭션을 직렬화한다.
                inserted = db.execute(
                    text(
                        "INSERT INTO job_submissions(actor_id,submission_key,fingerprint,"
                        "job_id,created_at) VALUES(:actor,:key,:fingerprint,NULL,:at) "
                        "ON CONFLICT(actor_id,submission_key) DO NOTHING RETURNING actor_id"
                    ),
                    {
                        "actor": owner_actor,
                        "key": idempotency_key,
                        "fingerprint": fingerprint,
                        "at": now(),
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    previous = (
                        db.execute(
                            text(
                                "SELECT fingerprint,job_id FROM job_submissions "
                                "WHERE actor_id=:actor AND submission_key=:key"
                            ),
                            {"actor": owner_actor, "key": idempotency_key},
                        )
                        .mappings()
                        .one()
                    )
                    if previous["fingerprint"] != fingerprint:
                        raise PlatformError("SOS-JOB-IDEMPOTENCY-CONFLICT")
                    previous_id = previous["job_id"]
                    # get_job은 새 트랜잭션으로 기존 확정 Job을 조회한다.
                    return self.get_job(previous_id)
            # Feature 조회·폐기의 접수는 같은 등록 Job 행 잠금으로 직렬화한다.
            # 폐기가 Queued인 순간부터 신규 조회를 차단하므로 Registry 삭제가
            # 이미 접수된 조회를 놓치거나 삭제 중 새 조회가 유입되는 경합을 없앤다.
            if kind in ("feature_retrieve", "feature_retire"):
                registration_id = spec["registration_job_id"]
                registration = db.execute(
                    text(
                        "SELECT kind,status,dataset_id,owner_actor FROM jobs "
                        "WHERE id=:id FOR UPDATE"
                    ),
                    {"id": registration_id},
                ).mappings().first()
                required_statuses = (
                    ("Succeeded",) if kind == "feature_retrieve"
                    else ("Succeeded", "Failed", "Cancelled")
                )
                if (
                    registration is None
                    or registration["kind"] != "feature_register"
                    or registration["status"] not in required_statuses
                    or registration["dataset_id"] != dataset_id
                    or registration["owner_actor"] != owner_actor
                ):
                    raise PlatformError("SOS-API-CONFLICT")
                retirement_exists = db.execute(
                    text(
                        "SELECT 1 FROM jobs AS r WHERE r.kind='feature_retire' "
                        "AND (r.status IN ('Queued','Running','Succeeded') "
                        "OR (:for_retrieval AND r.status='Failed' "
                        "AND EXISTS (SELECT 1 FROM attempts a WHERE a.job_id=r.id))) "
                        "AND r.spec::jsonb->>'registration_job_id'=:registration_id LIMIT 1"
                    ),
                    {
                        "registration_id": registration_id,
                        "for_retrieval": kind == "feature_retrieve",
                    },
                ).first()
                if retirement_exists:
                    raise PlatformError("SOS-API-CONFLICT")
            # 실패한 멱등성 요청의 새 테이블 예약은 DB에 남기지 않는다.
            if reserve_iceberg_table is not None:
                self._reserve_iceberg_table_in_transaction(db, reserve_iceberg_table, owner_actor)
            if is_understanding:
                existing_id = db.execute(
                    text(
                        "SELECT id FROM jobs WHERE dataset_id=:dataset AND owner_actor=:actor "
                        "AND kind='profile' AND spec=:spec "
                        "AND status IN ('Queued','Running') "
                        "ORDER BY created_at DESC,id DESC LIMIT 1"
                    ),
                    {"dataset": dataset_id, "actor": owner_actor, "spec": item["spec"]},
                ).scalar_one_or_none()
                if existing_id:
                    if idempotency_key is not None:
                        db.execute(
                            text(
                                "UPDATE job_submissions SET job_id=:job "
                                "WHERE actor_id=:actor AND submission_key=:key"
                            ),
                            {"job": existing_id, "actor": owner_actor, "key": idempotency_key},
                        )
                    return self.get_job(existing_id)
            db.execute(
                text(
                    "INSERT INTO jobs(id,kind,dataset_id,spec,status,created_at,updated_at,"
                    "owner_actor,request_id) "
                    "VALUES(:id,:kind,:dataset_id,:spec,:status,:created_at,:updated_at,"
                    ":owner_actor,:request_id)"
                ),
                item,
            )
            if idempotency_key is not None:
                db.execute(
                    text(
                        "UPDATE job_submissions SET job_id=:job "
                        "WHERE actor_id=:actor AND submission_key=:key"
                    ),
                    {"job": item["id"], "actor": owner_actor, "key": idempotency_key},
                )
            if owner_actor:
                self._audit(
                    db,
                    actor_id=owner_actor,
                    actor_type=actor_type,
                    action="job.create",
                    resource_type="job",
                    resource_id=item["id"],
                    request_id=request_id,
                )
        return self.get_job(item["id"])

    @staticmethod
    def _decode_job(item: dict) -> dict:
        """Job JSON과 취소 플래그를 API 응답 계약으로 변환한다."""
        item["spec"] = json.loads(item["spec"])
        item["result"] = json.loads(item["result"]) if item["result"] else None
        item["cancel_requested"] = bool(item["cancel_requested"])
        code = item.get("error_code")
        item["retry_policy"] = ERRORS[code].retry_policy if code in ERRORS else None
        if item.get("error") and code not in ERRORS:
            # 구버전 Job의 원문은 DB에서 보존하지만 API로 재노출하지 않는다.
            item["error"] = "이전 실행의 오류 기록입니다. 운영 진단에서 확인하세요."
        return item

    @staticmethod
    def _decode_attempt(item: dict) -> dict:
        if item.get("error") and item.get("error_code") not in ERRORS:
            item["error"] = "이전 실행의 상세 오류는 운영 기록에서 확인하세요."
        return item

    def get_job(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id})
                .mappings()
                .first()
            )
            if not row:
                return None
            item = self._decode_job(dict(row))
            item["attempts"] = [
                self._decode_attempt(dict(a))
                for a in db.execute(
                    text("SELECT * FROM attempts WHERE job_id=:id ORDER BY number"), {"id": job_id}
                ).mappings()
            ]
            return item

    def blocked_feature_registration_ids(
        self, job_ids: list[str], *, include_failed: bool = True
    ) -> set[str]:
        """접수 중·실행 중·완료된 폐기는 신규 조회를 허용하지 않는다."""
        if not job_ids:
            return set()
        statement = text(
            "SELECT r.spec::jsonb->>'registration_job_id' FROM jobs r "
            "WHERE r.kind='feature_retire' AND ("
            "r.status IN ('Queued','Running','Succeeded') "
            "OR (:include_failed AND r.status='Failed' AND EXISTS "
            "(SELECT 1 FROM attempts a WHERE a.job_id=r.id))) "
            "AND r.spec::jsonb->>'registration_job_id' IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        with self.connection() as db:
            return set(db.execute(
                statement, {"ids": job_ids, "include_failed": include_failed}
            ).scalars())

    def retired_feature_registration_ids(self, job_ids: list[str]) -> set[str]:
        """SOS에서 명시적으로 확정된 Feast 등록 폐기 Job만 인정한다."""
        if not job_ids:
            return set()
        query = text(
            "SELECT spec::jsonb->>'registration_job_id' FROM jobs "
            "WHERE kind='feature_retire' AND status='Succeeded' "
            "AND spec::jsonb->>'registration_job_id' IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        with self.connection() as db:
            return set(db.execute(query, {"ids": job_ids}).scalars())

    def jobs(
        self,
        *,
        limit: int = 20,
        before: dict | None = None,
        actor_id: str | None = None,
        kind: str | None = None,
        model_revision_id: str | None = None,
        exclude_kind: str | None = None,
        exclude_ml_train: bool = False,
    ) -> list[dict]:
        """목록은 종류별 Job 페이지와 각 Job의 마지막 Attempt 요약만 반환한다."""
        if not 1 <= limit <= 101:
            raise InvalidRequestError("Job 조회 개수가 허용 범위를 벗어났습니다.")
        if model_revision_id is not None and kind != "ml_playground":
            raise InvalidRequestError(
                "모델별 Job 이력은 단일 입력 점수 계산에서만 조회할 수 있습니다."
            )
        filters = []
        if before:
            filters.append("(created_at, id) < (:created_at, :id)")
        if actor_id is not None:
            filters.append("owner_actor=:actor_id")
        if kind is not None:
            filters.append("kind=:kind")
        if model_revision_id is not None:
            # 모델 필터를 DB 페이지네이션 이전에 적용한다. 후처리 필터는
            # 다른 모델 실행에 밀려 있는 이력을 '없음'으로 잘못 보여준다.
            filters.append("(spec::jsonb ->> 'model_revision_id')=:model_revision_id")
        if exclude_kind is not None:
            filters.append("kind!=:exclude_kind")
        if exclude_ml_train:
            filters.append(
                "kind NOT IN ('ml_pipeline_preflight','ml_train','ml_score','ml_playground','ml_policy','ml_evaluate','ml_explain')"
            )
        predicate = ("WHERE " + " AND ".join(filters)) if filters else ""
        query = text(
            f"SELECT * FROM jobs {predicate} ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        params = {
            "limit": limit,
            "actor_id": actor_id,
            "kind": kind,
            "model_revision_id": model_revision_id,
            "exclude_kind": exclude_kind,
            **(before or {}),
        }
        with self.connection() as db:
            rows = db.execute(query, params).mappings().all()
            items = [dict(row) for row in rows]
            if not items:
                return []
            # Window 함수로 전송되는 Attempt를 Job당 하나로 제한한다.
            query = text(
                "SELECT * FROM (SELECT a.*, "
                "COUNT(*) OVER(PARTITION BY a.job_id) AS attempt_count,"
                "ROW_NUMBER() OVER(PARTITION BY a.job_id ORDER BY a.number DESC) AS rank "
                "FROM attempts a WHERE a.job_id IN :ids) history WHERE rank=1"
            ).bindparams(bindparam("ids", expanding=True))
            summaries = db.execute(query, {"ids": [item["id"] for item in items]}).mappings()
            latest = {row["job_id"]: self._decode_attempt(dict(row)) for row in summaries}
        for item in items:
            self._decode_job(item)
            summary = latest.get(item["id"])
            item["attempt_count"] = summary["attempt_count"] if summary else 0
            item["latest_attempt"] = (
                {
                    key: value
                    for key, value in summary.items()
                    if key not in ("rank", "attempt_count")
                }
                if summary
                else None
            )
        return items

    def request_cancel(
        self, job_id: str, *, actor_id: str | None = None, actor_type: str = "human"
    ) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(
                    text("SELECT status,kind FROM jobs WHERE id=:id FOR UPDATE"), {"id": job_id}
                )
                .mappings()
                .first()
            )
            if row and row["status"] == "Queued":
                db.execute(
                    text("UPDATE jobs SET status='Cancelled',updated_at=:at WHERE id=:id"),
                    {"at": now(), "id": job_id},
                )
            elif row and row["status"] == "Running":
                if row["kind"] == "feature_retire":
                    raise FeatureRetirementCancellationConflict(
                        "진행 중 Feast 정의 폐기는 외부 삭제 커밋이 불확실해 취소할 수 없습니다."
                    )
                if row["kind"] == "iceberg_import":
                    raise IcebergCancellationConflict(
                        "실행 중 Iceberg 적재는 커밋 여부를 보장할 수 없어 취소할 수 없습니다. "
                        "완료 또는 중단 후 Snapshot을 확인하세요."
                    )
                db.execute(
                    text("UPDATE jobs SET cancel_requested=true,updated_at=:at WHERE id=:id"),
                    {"at": now(), "id": job_id},
                )
            if row and actor_id:
                self._audit(
                    db,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    action="job.cancel",
                    resource_type="job",
                    resource_id=job_id,
                )
        return self.get_job(job_id)

    def cancellation_requested(self, job_id: str) -> bool:
        """Worker가 전체 Job·Attempt를 읽지 않고 취소 플래그만 확인한다."""
        with self.connection() as db:
            value = db.execute(
                text("SELECT cancel_requested FROM jobs WHERE id=:id"), {"id": job_id}
            ).scalar_one_or_none()
        if value is None:
            raise RuntimeError("Job is missing during execution")
        return bool(value)

    def claim(self) -> dict | None:
        """행 잠금과 Worker 소유 세션으로 현재 Job을 한 Attempt가 인수한다."""
        with self.worker_ownership_connection() as db:
            row = (
                db.execute(
                    text(
                        "SELECT id FROM jobs WHERE status='Queued' "
                        "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            job_id = row["id"]
            number = db.execute(
                text("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE job_id=:id"),
                {"id": job_id},
            ).scalar_one()
            db.execute(
                text("UPDATE jobs SET status='Running',updated_at=:at WHERE id=:id"),
                {"at": now(), "id": job_id},
            )
            db.execute(
                text(
                    "INSERT INTO attempts(id,job_id,number,status,started_at) "
                    "VALUES(:id,:job,:number,'Running',:at)"
                ),
                {"id": str(uuid4()), "job": job_id, "number": number, "at": now()},
            )
        return self.get_job(job_id)

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        attempt_id: str,
        result: dict | None = None,
        error: str | None = None,
        publish: dict | None = None,
        write_id: str | None = None,
        model_publish: dict | None = None,
        scoring_publish: dict | None = None,
        policy_publish: dict | None = None,
        evaluation_publish: dict | None = None,
        explanation_publish: dict | None = None,
        error_code: str | None = None,
        failure_stage: str | None = None,
        failure_category: str | None = None,
        exception_type: str | None = None,
    ) -> str:
        # Job/Attempt 최종 상태를 검증하지 않으면 잘못된 내부 호출이 DB에
        # Queued·Running 같은 미완료 상태를 확정하고 복구 불가능하게 만들 수 있다.
        if status not in ("Succeeded", "Failed", "Cancelled"):
            raise ValueError("Invalid Job terminal status")
        if error_code is not None and error_code not in ERRORS:
            raise ValueError("Unregistered error code")
        with self.worker_ownership_connection() as db:
            row = (
                db.execute(
                    text(
                        "SELECT status,cancel_requested,owner_actor,request_id,kind,dataset_id,spec "
                        "FROM jobs WHERE id=:id FOR UPDATE"
                    ),
                    {"id": job_id},
                )
                .mappings()
                .first()
            )
            if not row or row["status"] != "Running":
                raise RuntimeError("Job is not running")
            # 이전 실행은 새 Claim으로 생성된 Attempt의 결과를 확정할 수 없다.
            active_attempt = db.execute(
                text(
                    "SELECT id FROM attempts WHERE job_id=:job_id AND status='Running' FOR UPDATE"
                ),
                {"job_id": job_id},
            ).scalar_one_or_none()
            if active_attempt != attempt_id:
                raise WorkerOwnershipLost("Job execution attempt has changed")
            if row["cancel_requested"]:
                status, result, publish = "Cancelled", None, None
                error, error_code, failure_stage = None, None, None
                failure_category, exception_type = None, None
            if status == "Succeeded" and publish:
                item = dict(
                    id=publish.get("retention_id") or str(uuid4()),
                    name=publish["name"],
                    kind=publish.get("kind", "file"),
                    location=publish["location"],
                    version=publish["version"],
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
                    self._publish_object(db, write_id, publish["location"], publish["version"])
                if publish.get("retention_id"):
                    self._confirm_snapshot_retention(
                        db, publish["retention_id"], publish["location"], publish["version"]
                    )
                result = {**(result or {}), "dataset_id": item["id"]}
            if status == "Succeeded" and model_publish is not None:
                if row["kind"] != "ml_train" or not write_id or publish is not None:
                    raise ValueError("Invalid model publication request")
                runtime = dict(model_publish["runtime"])
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
                    ).mappings().first()
                    if origin_id else None
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
                    "dataset_version": model_publish["dataset_version"],
                    "algorithm": model_publish["algorithm"],
                    "registry_key": (json.loads(row["spec"]) or {}).get("registry_key", "default"),
                    "location": model_publish["location"],
                    "sha256": model_publish["sha256"],
                    "features": json.dumps(model_publish["features"], allow_nan=False),
                    "params": json.dumps(model_publish["params"], allow_nan=False),
                    "training_score_summary": json.dumps(
                        model_publish["training_score_summary"], allow_nan=False
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
                self._publish_object(
                    db, write_id, model_publish["location"], model_publish["sha256"]
                )
                result = {**(result or {}), "model_revision_id": item["id"]}
            if status == "Succeeded" and scoring_publish is not None:
                if row["kind"] != "ml_score" or not write_id or publish is not None:
                    raise ValueError("Invalid scoring publication request")
                item = {
                    "id": str(uuid4()),
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "model_revision_id": scoring_publish["model_revision_id"],
                    "dataset_id": row["dataset_id"],
                    "dataset_version": scoring_publish["dataset_version"],
                    "location": scoring_publish["location"],
                    "sha256": scoring_publish["sha256"],
                    "row_count": scoring_publish["row_count"],
                    "score_direction": scoring_publish["score_direction"],
                    "feature_names": json.dumps(scoring_publish["feature_names"]),
                    "score_summary": json.dumps(scoring_publish["score_summary"], allow_nan=False),
                    "ranking_metrics": json.dumps(
                        scoring_publish["ranking_metrics"], allow_nan=False
                    )
                    if scoring_publish["ranking_metrics"] is not None
                    else None,
                    "label_column": scoring_publish["label_column"],
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
                self._publish_object(
                    db, write_id, scoring_publish["location"], scoring_publish["sha256"]
                )
                result = {**(result or {}), "scoring_result_id": item["id"]}
            if status == "Succeeded" and policy_publish is not None:
                if row["kind"] != "ml_policy":
                    raise ValueError("Invalid policy publication request")
                item = {
                    "id": str(uuid4()),
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "scoring_result_id": policy_publish["scoring_result_id"],
                    "mode": policy_publish["mode"],
                    "threshold": policy_publish["threshold"],
                    "alert_fraction": policy_publish["alert_fraction"],
                    "decision_summary": json.dumps(
                        policy_publish["decision_summary"], allow_nan=False
                    ),
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
            if status == "Succeeded" and evaluation_publish is not None:
                if row["kind"] != "ml_evaluate":
                    raise ValueError("Invalid evaluation publication request")
                item = {
                    "id": str(uuid4()),
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "scoring_result_id": evaluation_publish["scoring_result_id"],
                    "policy_revision_id": evaluation_publish["policy_revision_id"],
                    "label_coverage": evaluation_publish["label_coverage"],
                    "decision_metrics": json.dumps(
                        evaluation_publish["decision_metrics"], allow_nan=False
                    ),
                    "ranking_metrics": json.dumps(
                        evaluation_publish["ranking_metrics"], allow_nan=False
                    )
                    if evaluation_publish["ranking_metrics"] is not None
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
            if status == "Succeeded" and explanation_publish is not None:
                if row["kind"] != "ml_explain":
                    raise ValueError("Invalid explanation publication request")
                item = {
                    "id": str(uuid4()),
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "scoring_result_id": explanation_publish.get("scoring_result_id"),
                    "playground_job_id": explanation_publish.get("playground_job_id"),
                    "model_revision_id": explanation_publish["model_revision_id"],
                    "row_index": explanation_publish.get("row_index"),
                    "method": explanation_publish["method"],
                    "output": json.dumps(explanation_publish, allow_nan=False),
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
            if (
                status in ("Succeeded", "Failed", "Cancelled")
                and row["kind"] == "profile"
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
            db.execute(
                text(
                    "UPDATE jobs SET status=:status,result=:result,error=:error,"
                    "error_code=:error_code,failure_stage=:failure_stage,"
                    "failure_category=:failure_category,exception_type=:exception_type,"
                    "updated_at=:at WHERE id=:id"
                ),
                dict(
                    id=job_id,
                    status=status,
                    result=json.dumps(result, default=str) if result is not None else None,
                    error=error,
                    error_code=error_code,
                    failure_stage=failure_stage,
                    failure_category=failure_category,
                    exception_type=exception_type,
                    at=now(),
                ),
            )
            completed_attempt = db.execute(
                text(
                    "UPDATE attempts SET status=:status,finished_at=:at,error=:error,"
                    "error_code=:error_code,failure_stage=:failure_stage,"
                    "failure_category=:failure_category,exception_type=:exception_type "
                    "WHERE job_id=:id AND id=:attempt_id AND status='Running' RETURNING id"
                ),
                dict(
                    id=job_id,
                    attempt_id=attempt_id,
                    status=status,
                    at=now(),
                    error=error,
                    error_code=error_code,
                    failure_stage=failure_stage,
                    failure_category=failure_category,
                    exception_type=exception_type,
                ),
            ).scalar_one()
            reason = f"attempt={completed_attempt}"
            if status == "Succeeded" and publish:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="dataset.publish",
                    resource_type="dataset",
                    resource_id=item["id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            if status == "Succeeded" and policy_publish is not None:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="policy.publish",
                    resource_type="decision_policy",
                    resource_id=result["policy_revision_id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            if status == "Succeeded" and explanation_publish is not None:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="explanation.publish",
                    resource_type="explanation",
                    resource_id=result["explanation_result_id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            if status == "Succeeded" and evaluation_publish is not None:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="evaluation.publish",
                    resource_type="evaluation",
                    resource_id=result["evaluation_revision_id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            if status == "Succeeded" and scoring_publish is not None:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="scoring.publish",
                    resource_type="scoring_result",
                    resource_id=result["scoring_result_id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            if status == "Succeeded" and model_publish is not None:
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="model.publish",
                    resource_type="model",
                    resource_id=result["model_revision_id"],
                    reason=f"job={job_id};{reason}",
                    request_id=row["request_id"],
                )
            # 외부 기록은 Job 트랜잭션과 함께 '전송 대기'만 남긴다.
            # MLflow 네트워크 호출을 여기서 실행하면 데이터 정본을 오염시킨다.
            if (
                status == "Succeeded"
                and row["kind"]
                in ("ml_train", "ml_score", "ml_policy", "ml_evaluate", "ml_explain")
                and os.getenv("SOS_MLFLOW_TRACKING_URI")
            ):
                db.execute(
                    text(
                        "INSERT INTO mlflow_exports"
                        "(job_id,state,run_id,attempts,last_error_code,retry_after,"
                        "created_at,updated_at) "
                        "VALUES (:job,'Pending',NULL,0,NULL,:at,:at,:at) "
                        "ON CONFLICT (job_id) DO NOTHING"
                    ),
                    {"job": job_id, "at": now()},
                )
            self._audit(
                db,
                actor_id="sos-worker",
                actor_type="service",
                action="job.finish",
                resource_type="job",
                resource_id=job_id,
                outcome={
                    "Succeeded": "succeeded",
                    "Failed": "failed",
                    "Cancelled": "cancelled",
                }.get(status, status.lower()),
                reason=reason,
                request_id=row["request_id"],
            )
        return status

    def recover(self) -> list[tuple[str, str]]:
        """미완료 실행을 복구하고 정리 가능한 로컬 Attempt 식별자를 반환한다."""
        recovered: list[tuple[str, str]] = []
        # 새 잠금 소유자가 복구 도중 소유권을 잃으면 전체 복구를 롤백한다.
        with self.worker_ownership_connection() as db:
            rows = (
                db.execute(
                    text(
                        "SELECT id,cancel_requested,kind,request_id "
                        "FROM jobs WHERE status='Running' FOR UPDATE"
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                # Iceberg 적재는 Worker가 죽기 직전에 커밋됐을 가능성이 있다.
                # 자동 재시도하면 동일 데이터를 두 번 추가할 수 있으므로 명시적 확인을 요구한다.
                unsafe = row["kind"] == "iceberg_import"
                status = (
                    "Failed" if unsafe else "Cancelled" if row["cancel_requested"] else "Queued"
                )
                message = (
                    "Iceberg 적재가 중단됐습니다. Snapshot을 확인한 뒤 재실행하세요."
                    if unsafe
                    else None
                )
                db.execute(
                    text(
                        "UPDATE jobs SET status=:s,error=:error,error_code=:code,"
                        "failure_stage=:stage,failure_category='process_exit',"
                        "exception_type=NULL,updated_at=:at WHERE id=:id"
                    ),
                    {
                        "s": status,
                        "error": message,
                        "at": now(),
                        "id": row["id"],
                        "code": "SOS-ICEBERG-UNCERTAIN" if unsafe else None,
                        "stage": "iceberg_commit" if unsafe else None,
                    },
                )
                # 이전 Worker 소유권이 종료된 실행의 미확정 S3 게시 의도를 해소한다.
                db.execute(
                    text(
                        "UPDATE object_writes SET state='Failed',updated_at=:at,"
                        "error='Worker interrupted before publication' "
                        "WHERE owner_kind='attempt' AND state='Pending' "
                        "AND owner_id IN (SELECT id FROM attempts "
                        "WHERE job_id=:id AND status='Running')"
                    ),
                    {"at": now(), "id": row["id"]},
                )
                interrupted = (
                    db.execute(
                        text(
                            "UPDATE attempts SET status='Interrupted',finished_at=:at,error=:error,"
                            "error_code='SOS-JOB-INTERRUPTED',failure_stage='worker_recovery',"
                            "failure_category='process_exit',exception_type=NULL "
                            "WHERE job_id=:id AND status='Running' RETURNING id"
                        ),
                        {"at": now(), "error": "Worker stopped before completion", "id": row["id"]},
                    )
                    .scalars()
                    .all()
                )
                recovered.extend((row["id"], item) for item in interrupted)
                attempt_id = interrupted[-1] if interrupted else "unknown"
                if unsafe:
                    # 자동 재시도 대신 Catalog 속성으로 커밋 여부를 추적할 근거를 남긴다.
                    db.execute(
                        text("UPDATE jobs SET error=:error WHERE id=:id"),
                        {
                            "id": row["id"],
                            "error": (
                                f"Iceberg 적재 중단: Attempt {attempt_id}의 "
                                "sos.attempt-id를 Catalog Snapshot과 대조하세요. "
                                "확인 전 다시 적재하지 마세요."
                            ),
                        },
                    )
                self._audit(
                    db,
                    actor_id="sos-worker",
                    actor_type="service",
                    action="job.recover",
                    resource_type="job",
                    resource_id=row["id"],
                    outcome={
                        "Queued": "requeued",
                        "Cancelled": "cancelled",
                        "Failed": "interrupted",
                    }[status],
                    reason=f"kind={row['kind']};attempt={attempt_id}"[:200],
                    request_id=row["request_id"],
                )

        return recovered
