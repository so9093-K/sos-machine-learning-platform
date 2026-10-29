"""실행 결과 게시와 Job 종료를 한 트랜잭션으로 조합한다."""

from collections.abc import Mapping

from sqlalchemy import Connection

from .result_publication import (
    publish_dataset,
    publish_evaluation,
    publish_explanation,
    publish_model,
    publish_policy,
    publish_profile,
    publish_scoring,
)
from .store import Store


def complete_job(
    store: Store,
    job_id: str,
    status: str,
    *,
    attempt_id: str,
    result: dict | None = None,
    publication: dict | None = None,
    write_id: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    failure_stage: str | None = None,
    failure_category: str | None = None,
    exception_type: str | None = None,
) -> str:
    """잠금·취소 판정 이후 실제 종료 상태에 맞는 결과만 게시한다.

    게시 함수는 Job 저장소가 제공한 연결을 사용한다. 자산·쓰기 의도·Tracking
    Outbox·감사·Job·Attempt 중 하나라도 실패하면 동일 트랜잭션 전체가 롤백된다.
    외부 객체 업로드와 SDK 커밋의 성공은 이 DB 트랜잭션의 성공과 별개다.
    """

    def on_finish(db: Connection, row: Mapping, final_status: str, value: dict | None):
        if final_status == "Succeeded":
            kind = row["kind"]
            if kind == "ml_train":
                publisher = publish_model
            elif kind == "ml_score":
                publisher = publish_scoring
            elif kind == "ml_policy":
                publisher = publish_policy
            elif kind == "ml_evaluate":
                publisher = publish_evaluation
            elif kind == "ml_explain":
                publisher = publish_explanation
            elif kind in ("transform", "feature_retrieve", "iceberg_import"):
                publisher = publish_dataset
            else:
                publisher = None
            if publisher is not None:
                payload = publication if publication is not None else value
                if payload is None:
                    raise ValueError("Successful result is missing publication data")
                value = publisher(store, db, row, job_id, attempt_id, value, payload, write_id)
            elif publication is not None or write_id is not None:
                raise ValueError("Job type does not publish artifacts")
            store.enqueue_tracking_export(db, job_id, kind)
        publish_profile(db, row, job_id, value)
        return value

    return store.finish(
        job_id,
        status,
        attempt_id=attempt_id,
        result=result,
        on_finish=on_finish,
        error=error,
        error_code=error_code,
        failure_stage=failure_stage,
        failure_category=failure_category,
        exception_type=exception_type,
    )
