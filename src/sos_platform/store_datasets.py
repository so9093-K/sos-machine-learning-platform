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
        parse_options: dict | None = None,
        actor_type: str = "human",
    ) -> dict:
        item = dict(
            id=dataset_id or str(uuid4()),
            owner_actor=owner_actor,
            name=name,
            kind=kind,
            location=location,
            version=str(version),
            parse_options=json.dumps(parse_options, sort_keys=True)
            if parse_options is not None
            else None,
            created_at=now(),
        )
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO datasets(id,name,kind,location,version,parse_options,created_at,owner_actor) "
                    "VALUES(:id,:name,:kind,:location,:version,:parse_options,:created_at,:owner_actor)"
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

    def find_dataset_by_name_version(
        self, *, name: str, version: str, actor_id: str | None = None
    ) -> dict | None:
        """예제 중복 등록 시 전체 Dataset 목록을 불러오지 않고 바로 조회한다."""
        owner_filter = " AND owner_actor=:actor_id" if actor_id is not None else ""
        with self.connection() as db:
            row = (
                db.execute(
                    text(
                        "SELECT * FROM datasets WHERE name=:name AND version=:version"
                        + owner_filter
                        + " ORDER BY created_at DESC,id DESC LIMIT 1"
                    ),
                    {"name": name, "version": version, "actor_id": actor_id},
                )
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

    def iceberg_write_claim(self, table_name: str) -> dict | None:
        """테이블 쓰기 권한은 읽기용 Dataset 참조와 독립적으로 보존한다."""
        with self.connection() as db:
            item = (
                db.execute(
                    text("SELECT * FROM iceberg_table_owners WHERE table_name=:name"),
                    {"name": f"default.{table_name}"},
                )
                .mappings()
                .first()
            )
        return dict(item) if item else None

    @staticmethod
    def _reserve_iceberg_table_in_transaction(db, table_name: str, actor_id: str) -> dict:
        """Job·테이블 예약을 같은 DB 트랜잭션으로 묶는다."""
        name = f"default.{table_name}"
        db.execute(
            text(
                "INSERT INTO iceberg_table_owners"
                "(table_name,owner_actor,token,state,created_at) "
                "VALUES(:name,:owner,:token,'Reserved',:at) "
                "ON CONFLICT(table_name) DO NOTHING"
            ),
            {"name": name, "owner": actor_id, "token": str(uuid4()), "at": now()},
        )
        claim = (
            db.execute(
                text("SELECT * FROM iceberg_table_owners WHERE table_name=:name"),
                {"name": name},
            )
            .mappings()
            .one()
        )
        if claim["owner_actor"] != actor_id or claim["state"] not in ("Reserved", "Active"):
            raise PermissionError("Iceberg table write access denied")
        return dict(claim)

    def reserve_iceberg_table(self, table_name: str, actor_id: str) -> dict:
        """고유 테이블명에 대한 쓰기 소유권을 PostgreSQL에서 원자적으로 예약한다."""
        with self.connection() as db:
            return self._reserve_iceberg_table_in_transaction(db, table_name, actor_id)

    def activate_iceberg_table(
        self, table_name: str, actor_id: str, token: str, table_uuid: str
    ) -> None:
        """Catalog의 토큰과 물리 Table UUID를 묶어 같은 테이블만 활성화한다."""
        with self.connection() as db:
            changed = db.execute(
                text(
                    "UPDATE iceberg_table_owners SET state='Active',table_uuid=:uuid "
                    "WHERE table_name=:name AND owner_actor=:actor AND token=:token "
                    "AND ((state='Reserved' AND (table_uuid IS NULL OR table_uuid=:uuid)) "
                    "OR (state='Active' AND table_uuid=:uuid))"
                ),
                {
                    "name": f"default.{table_name}",
                    "actor": actor_id,
                    "token": token,
                    "uuid": table_uuid,
                },
            )
            if changed.rowcount != 1:
                raise PermissionError("Iceberg table identity or ownership changed")

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
