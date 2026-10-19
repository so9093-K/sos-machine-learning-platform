"""MLflow 외부 전송의 영속적 Outbox. Job 상태는 절대 변경하지 않는다."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from .store_administration import now


class TrackingStoreMixin:
    def tracking_status(self, job_id: str) -> dict | None:
        """외부 URI·인증값 없이 전송 상태와 Run ID만 제공한다."""
        with self.connection() as db:
            row = db.execute(
                text(
                    "SELECT job_id,state,run_id,attempts,last_error_code,retry_after "
                    "FROM mlflow_exports WHERE job_id=:job"
                ),
                {"job": job_id},
            ).mappings().first()
        return dict(row) if row else None

    def claim_tracking_export(self) -> dict | None:
        """한 Worker 세션이 만료된 전송을 한 번만 소유한다."""
        with self.worker_ownership_connection() as db:
            row = db.execute(
                text(
                    "SELECT job_id,run_id,attempts FROM mlflow_exports "
                    "WHERE state IN ('Pending','Retry') AND retry_after<=:at "
                    "ORDER BY retry_after,job_id LIMIT 1 FOR UPDATE SKIP LOCKED"
                ),
                {"at": now()},
            ).mappings().first()
            if row is None:
                return None
            db.execute(
                text(
                    "UPDATE mlflow_exports SET state='Sending',attempts=attempts+1,"
                    "updated_at=:at WHERE job_id=:job"
                ),
                {"job": row["job_id"], "at": now()},
            )
            return {**dict(row), "attempts": row["attempts"] + 1}

    def save_tracking_run_id(self, job_id: str, run_id: str) -> None:
        """외부 생성 Run ID를 Metrics 전송 전에 복구 가능하게 기록한다."""
        with self.worker_ownership_connection() as db:
            changed = db.execute(
                text(
                    "UPDATE mlflow_exports SET run_id=:run,updated_at=:at "
                    "WHERE job_id=:job AND state='Sending' AND "
                    "(run_id IS NULL OR run_id=:run) RETURNING job_id"
                ),
                {"job": job_id, "run": run_id, "at": now()},
            ).scalar_one_or_none()
            if changed is None:
                raise RuntimeError("Tracking export ownership changed")

    def finish_tracking_export(self, job_id: str, *, error_code: str | None = None) -> None:
        """실패는 재시도로, 성공은 외부 전송 완료로만 기록한다."""
        with self.worker_ownership_connection() as db:
            row = db.execute(
                text(
                    "SELECT attempts FROM mlflow_exports "
                    "WHERE job_id=:job AND state='Sending' FOR UPDATE"
                ),
                {"job": job_id},
            ).mappings().first()
            if row is None:
                raise RuntimeError("Tracking export ownership changed")
            delay = min(2 ** min(row["attempts"], 8), 300)
            due = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            db.execute(
                text(
                    "UPDATE mlflow_exports SET state=:state,last_error_code=:error,"
                    "retry_after=:retry,updated_at=:at WHERE job_id=:job"
                ),
                {
                    "state": "Retry" if error_code else "Succeeded",
                    "error": error_code,
                    "retry": due if error_code else now(),
                    "at": now(),
                    "job": job_id,
                },
            )

    def recover_tracking_exports(self) -> int:
        """Worker 비정상 종료 당시 Sending이던 전송을 조회·재시도 상태로 복구한다."""
        with self.worker_ownership_connection() as db:
            changed = db.execute(
                text(
                    "UPDATE mlflow_exports SET state='Retry',retry_after=:at,"
                    "last_error_code='SOS-MLFLOW-INTERRUPTED',updated_at=:at "
                    "WHERE state='Sending'"
                ),
                {"at": now()},
            )
            return changed.rowcount
