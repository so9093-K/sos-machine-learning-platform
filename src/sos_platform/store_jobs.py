"""Job 제출·멱등성·Attempt 상태 변경과 중단 복구의 PostgreSQL 정본."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import bindparam, text

from .errors import ERRORS, InvalidRequestError, PlatformError, WorkerOwnershipLost
from .store_administration import now


class IcebergCancellationConflict(Exception):
    """외부 Iceberg 커밋 완료 여부가 불명확할 때 취소를 거부한다."""


class JobStoreMixin:
    """Store의 공통 트랜잭션 경계를 통해 작업 생명주기를 관리한다."""

    def create_job(
        self,
        *,
        kind: str,
        dataset_id: str,
        spec: dict,
        owner_actor: str | None = None,
        actor_type: str = "human",
        request_id: str | None = None,
        idempotency_key: str | None = None,
        reserve_iceberg_table: str | None = None,
    ) -> dict:
        """중복 제출을 PostgreSQL 유일성 제약과 같은 트랜잭션으로 처리한다."""
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

    def jobs(
        self,
        *,
        limit: int = 20,
        before: dict | None = None,
        actor_id: str | None = None,
        kind: str | None = None,
        exclude_kind: str | None = None,
    ) -> list[dict]:
        """목록은 종류별 Job 페이지와 각 Job의 마지막 Attempt 요약만 반환한다."""
        if not 1 <= limit <= 101:
            raise InvalidRequestError("Job query limit is out of range")
        filters = []
        if before:
            filters.append("(created_at, id) < (:created_at, :id)")
        if actor_id is not None:
            filters.append("owner_actor=:actor_id")
        if kind is not None:
            filters.append("kind=:kind")
        if exclude_kind is not None:
            filters.append("kind!=:exclude_kind")
        predicate = ("WHERE " + " AND ".join(filters)) if filters else ""
        query = text(
            f"SELECT * FROM jobs {predicate} ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        params = {
            "limit": limit,
            "actor_id": actor_id,
            "kind": kind,
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
                )
                db.execute(
                    text(
                        "INSERT INTO datasets(id,name,kind,location,version,created_at,owner_actor) "
                        "VALUES(:id,:name,:kind,:location,:version,:created_at,:owner_actor)"
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
            if (
                status == "Succeeded"
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
