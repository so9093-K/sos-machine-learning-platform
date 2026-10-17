"""Iceberg Snapshot 보존 의도와 PostgreSQL 게시 상태를 별도로 유지한다."""

from sqlalchemy import text

from .store_administration import now


class SnapshotStoreMixin:
    """Store의 공통 DB 트랜잭션 경계를 이용하는 책임별 저장 계층."""

    def begin_snapshot_retention(
        self, *, dataset_id: str, location: str, snapshot_id: str, tag_name: str
    ) -> None:
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO snapshot_retention"
                    "(dataset_id,location,snapshot_id,tag_name,state,created_at,updated_at) "
                    "VALUES(:dataset_id,:location,:snapshot_id,:tag_name,'Pending',:at,:at) "
                    "ON CONFLICT(dataset_id) DO NOTHING"
                ),
                {
                    "dataset_id": dataset_id,
                    "location": location,
                    "snapshot_id": snapshot_id,
                    "tag_name": tag_name,
                    "at": now(),
                },
            )
            row = (
                db.execute(
                    text(
                        "SELECT location,snapshot_id,tag_name,state FROM snapshot_retention "
                        "WHERE dataset_id=:id FOR UPDATE"
                    ),
                    {"id": dataset_id},
                )
                .mappings()
                .one()
            )
            if (
                row["location"] != location
                or row["snapshot_id"] != snapshot_id
                or row["tag_name"] != tag_name
            ):
                raise RuntimeError("Snapshot reservation mismatch")

    def fail_snapshot_retention(self, dataset_id: str, error: str) -> None:
        with self.connection() as db:
            db.execute(
                text(
                    "UPDATE snapshot_retention SET state='Uncertain',error=:error,"
                    "updated_at=:at WHERE dataset_id=:id AND state='Pending'"
                ),
                {"id": dataset_id, "error": error[:200], "at": now()},
            )

    def snapshot_retention(self, dataset_id: str) -> dict | None:
        with self.connection() as db:
            result = (
                db.execute(
                    text("SELECT * FROM snapshot_retention WHERE dataset_id=:id"),
                    {"id": dataset_id},
                )
                .mappings()
                .first()
            )
            return dict(result) if result else None

    def unpublished_snapshot_retention(self, *, limit: int = 50) -> list[dict]:
        """Dataset 게시에 실패한 보존 의도를 조회한다. 참조·Tag를 변경하지 않는다."""
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    text(
                        "SELECT r.* FROM snapshot_retention r "
                        "LEFT JOIN datasets d ON d.id=r.dataset_id "
                        "WHERE d.id IS NULL ORDER BY r.created_at DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                ).mappings()
            ]

    @staticmethod
    def _confirm_snapshot_retention(db, dataset_id: str, location: str, snapshot_id: str) -> None:
        updated = db.execute(
            text(
                "UPDATE snapshot_retention SET state='Retained',updated_at=:at,error=NULL "
                "WHERE dataset_id=:id AND location=:location AND snapshot_id=:snapshot_id "
                "AND state IN ('Pending','Uncertain','Retained')"
            ),
            {"id": dataset_id, "location": location, "snapshot_id": str(snapshot_id), "at": now()},
        )
        if updated.rowcount != 1:
            raise RuntimeError("Snapshot protection intent is not ready")

    def confirm_existing_retention(
        self, dataset_id: str, *, actor_id: str, actor_type: str
    ) -> None:
        """기존 Dataset 보호 확인과 감사 기록을 하나의 DB 트랜잭션으로 확정한다."""
        with self.connection() as db:
            item = (
                db.execute(
                    text("SELECT location,version FROM datasets WHERE id=:id AND kind='iceberg'"),
                    {"id": dataset_id},
                )
                .mappings()
                .first()
            )
            if item is None:
                raise ValueError("Iceberg Dataset not found")
            self._confirm_snapshot_retention(db, dataset_id, item["location"], item["version"])
            self._audit(
                db,
                actor_id=actor_id,
                actor_type=actor_type,
                action="snapshot.retain",
                resource_type="dataset",
                resource_id=dataset_id,
            )
