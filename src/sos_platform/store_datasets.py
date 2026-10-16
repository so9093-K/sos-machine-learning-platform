"""Dataset 등록·소유권·키셋 페이지 및 결과 프로파일 조회를 담당한다."""

import json
from uuid import uuid4

from sqlalchemy import text

from .store_administration import now


class DatasetStoreMixin:
    """Store의 공통 DB 트랜잭션 경계를 이용하는 책임별 저장 계층."""

    def register_dataset(
        self,
        *,
        name: str,
        kind: str,
        location: str,
        version: str,
        write_id: str | None = None,
        dataset_id: str | None = None,
        owner_actor: str | None = None,
        retention_id: str | None = None,
        actor_type: str = "human",
    ) -> dict:
        item = dict(
            id=dataset_id or str(uuid4()),
            owner_actor=owner_actor,
            name=name,
            kind=kind,
            location=location,
            version=str(version),
            created_at=now(),
        )
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO datasets(id,name,kind,location,version,created_at,owner_actor) "
                    "VALUES(:id,:name,:kind,:location,:version,:created_at,:owner_actor)"
                ),
                item,
            )
            if write_id:
                self._publish_object(db, write_id, location, version)
            if retention_id:
                self._confirm_snapshot_retention(db, retention_id, location, version)
            if owner_actor:
                self._audit(
                    db,
                    actor_id=owner_actor,
                    actor_type=actor_type,
                    action="dataset.create",
                    resource_type="dataset",
                    resource_id=item["id"],
                )
        return item

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            row = (
                db.execute(text("SELECT * FROM datasets WHERE id=:id"), {"id": dataset_id})
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def owns_iceberg_table(self, table_name: str, actor_id: str) -> bool:
        """명시적인 기존 Iceberg Dataset 소유권만 테이블 접근 근거로 사용한다."""
        with self.connection() as db:
            return bool(
                db.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM datasets "
                        "WHERE kind='iceberg' AND location=:table AND owner_actor=:actor)"
                    ),
                    {"table": f"default.{table_name}", "actor": actor_id},
                ).scalar_one()
            )

    def dataset_page(
        self, *, limit: int, before: dict | None = None, actor_id: str | None = None
    ) -> list[dict]:
        """안정적인 created_at+id 키셋으로 데이터셋을 조회한다."""
        if not 1 <= limit <= 101:
            raise ValueError("Dataset page limit is out of range")
        conditions = []
        params = {"limit": limit}
        if actor_id is not None:
            conditions.append("owner_actor=:actor_id")
            params["actor_id"] = actor_id
        if before is not None:
            conditions.append("(created_at,id) < (:created_at,:id)")
            params.update(before)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(
                        f"SELECT * FROM datasets{where} "
                        "ORDER BY created_at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                ).mappings()
            ]

    def latest_dataset_profile(self, dataset_id: str, version: str) -> dict | None:
        """현재 원본 판본에 대한 최신 완료 Profile을 조회한다."""
        with self.connection() as db:
            row = db.execute(
                text(
                    "SELECT result FROM dataset_profiles "
                    "WHERE dataset_id=:dataset_id AND dataset_version=:version "
                    "ORDER BY created_at DESC, job_id DESC LIMIT 1"
                ),
                {"dataset_id": dataset_id, "version": version},
            ).scalar_one_or_none()
        return json.loads(row) if row is not None else None

    def latest_understanding_job(self, dataset_id: str) -> dict | None:
        """프로파일 실행 중/실패 상태를 기존 Job의 정본에서 읽는다."""
        with self.connection() as db:
            job_id = db.execute(
                text(
                    "SELECT id FROM jobs WHERE dataset_id=:id AND kind='profile' "
                    "AND spec::jsonb->>'mode'='understanding' "
                    "ORDER BY created_at DESC,id DESC LIMIT 1"
                ),
                {"id": dataset_id},
            ).scalar_one_or_none()
        return self.get_job(job_id) if job_id else None

