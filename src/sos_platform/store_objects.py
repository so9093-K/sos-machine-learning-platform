"""객체 게시 의도·관측·참조 대조를 담당한다. 원본 객체 삭제 기능을 포함하지 않는다."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import bindparam, text

from .store_administration import now


class ObjectStoreMixin:
    """Store의 공통 DB 트랜잭션 경계를 이용하는 책임별 저장 계층."""

    def begin_object_write(self, *, location: str, owner_kind: str, owner_id: str) -> str:
        """S3에 쓰기 전에 게시 의도를 DB에 확정한다."""
        if owner_kind not in ("upload", "attempt"):
            raise ValueError("Invalid object owner")
        write_id = str(uuid4())
        with self.connection() as db:
            db.execute(
                text(
                    "INSERT INTO object_writes(id,location,owner_kind,owner_id,state,created_at,updated_at) "
                    "VALUES(:id,:location,:owner_kind,:owner_id,'Pending',:at,:at)"
                ),
                {
                    "id": write_id,
                    "location": location,
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "at": now(),
                },
            )
        return write_id

    def fail_object_write(self, write_id: str, error: str | None = None) -> None:
        """성공 게시된 객체는 실패로 되돌리지 않는다."""
        with self.connection() as db:
            db.execute(
                text(
                    "UPDATE object_writes SET state='Failed',error=:error,updated_at=:at "
                    "WHERE id=:id AND state='Pending'"
                ),
                {"id": write_id, "error": error[:500] if error else None, "at": now()},
            )

    @staticmethod
    def _publish_object(db, write_id: str, location: str, version: str) -> None:
        updated = db.execute(
            text(
                "UPDATE object_writes SET state='Published',version=:version,updated_at=:at "
                "WHERE id=:id AND location=:location AND state='Pending'"
            ),
            {"id": write_id, "location": location, "version": version, "at": now()},
        )
        if updated.rowcount != 1:
            raise RuntimeError("Object publication intent is not pending")

    def object_writes(self, location: str) -> list[dict]:
        """진단에서 게시 기록을 조회한다."""
        with self.connection() as db:
            return [
                dict(item)
                for item in db.execute(
                    text(
                        "SELECT * FROM object_writes WHERE location=:location ORDER BY created_at"
                    ),
                    {"location": location},
                ).mappings()
            ]

    def observe_objects(self, objects: list[dict], observation_hours: int) -> dict:
        """객체 세대가 변경되면 이전 관측 횟수를 폐기하고 새로 집계한다.

        목록 ETag는 멀티파트 등에서 콘텐츠 해시가 아닐 수 있다. 따라서
        ETag·수정시각·크기를 결합한 값은 삭제 증거가 아닌 진단 단서다.
        """
        if not objects:
            return {}
        locations = [item["location"] for item in objects]
        cutoff = (datetime.now(UTC) - timedelta(hours=observation_hours)).isoformat()
        statement = text("SELECT location FROM datasets WHERE location IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        writes_query = text("SELECT * FROM object_writes WHERE location IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        with self.connection() as db:
            referenced = set(db.execute(statement, {"ids": locations}).scalars())
            writes = db.execute(writes_query, {"ids": locations}).mappings().all()
            by_location: dict[str, list[dict]] = {location: [] for location in locations}
            for write in writes:
                by_location[write["location"]].append(dict(write))
            owner_ids = [item["owner_id"] for item in writes if item["owner_kind"] == "attempt"]
            attempts = {}
            if owner_ids:
                statement = text("SELECT id,status FROM attempts WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                )
                attempts = dict(db.execute(statement, {"ids": owner_ids}).all())
            # 모든 객체에 동일한 관측 시각을 적용하고 한 번의 executemany로 보낸다.
            at = now()
            db.execute(
                text(
                    "INSERT INTO object_observations"
                    "(location,fingerprint,first_seen_at,last_seen_at,observation_count) "
                    "VALUES(:location,:fingerprint,:at,:at,1) "
                    "ON CONFLICT(location) DO UPDATE SET "
                    "fingerprint=excluded.fingerprint,"
                    "first_seen_at=CASE WHEN object_observations.fingerprint "
                    "IS DISTINCT FROM excluded.fingerprint THEN excluded.first_seen_at "
                    "ELSE object_observations.first_seen_at END,"
                    "observation_count=CASE WHEN object_observations.fingerprint "
                    "IS DISTINCT FROM excluded.fingerprint THEN 1 "
                    "WHEN object_observations.last_seen_at<=:cutoff "
                    "THEN object_observations.observation_count+1 "
                    "ELSE object_observations.observation_count END,"
                    "last_seen_at=CASE WHEN object_observations.fingerprint "
                    "IS DISTINCT FROM excluded.fingerprint "
                    "OR object_observations.last_seen_at<=:cutoff "
                    "THEN excluded.last_seen_at ELSE object_observations.last_seen_at END"
                ),
                [
                    {
                        "location": item["location"],
                        "fingerprint": item.get("fingerprint"),
                        "at": at,
                        "cutoff": cutoff,
                    }
                    for item in objects
                ],
            )
            observations_query = text(
                "SELECT * FROM object_observations WHERE location IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            observations = {
                item["location"]: dict(item)
                for item in db.execute(observations_query, {"ids": locations}).mappings()
            }
        return {
            location: {
                "referenced": location in referenced,
                "writes": by_location[location],
                "attempts": attempts,
                "observation": observations[location],
            }
            for location in locations
        }
