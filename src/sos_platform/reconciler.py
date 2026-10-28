"""S3 객체 목록과 PostgreSQL 게시·소유권 기록을 안전하게 대조한다.

이 모듈은 버킷 객체를 삭제하지 않는다. 현재 API에서 반환하는 후보는
자동 삭제 허가가 아니며, DB 또는 S3를 검사할 수 없으면 진단은 실패한다.
"""

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta

from .config import StoragePolicy
from .errors import InvalidRequestError
from .object_store import ObjectStore
from .store import Store

PREFIXES = ("datasets/", "results/")


def _position(cursor: str | None) -> tuple[str, str | None]:
    if cursor is None:
        return PREFIXES[0], None
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        position = json.loads(raw)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise InvalidRequestError(
            "저장소 점검 목록의 페이지 위치가 올바르지 않습니다. 목록을 새로 조회하세요."
        ) from exc
    if (
        not isinstance(position, dict)
        or set(position) != {"prefix", "token"}
        or position["prefix"] not in PREFIXES
        or (
            position["token"] is not None
            and (not isinstance(position["token"], str) or not position["token"])
        )
    ):
        raise InvalidRequestError(
            "저장소 점검 목록의 페이지 위치가 올바르지 않습니다. 목록을 새로 조회하세요."
        )
    return position["prefix"], position["token"]


def _encode_position(prefix: str, token: str | None) -> str:
    payload = json.dumps({"prefix": prefix, "token": token}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def classify_object(item: dict, metadata: dict, policy: StoragePolicy) -> tuple[str, str]:
    """S3 데이터의 삭제 가능성이 아니라 관측 시점의 보호·검토 상태를 반환한다."""
    if metadata["referenced"]:
        return "protected", "Dataset 또는 Model Revision이 이 객체를 참조하고 있습니다."
    writes = metadata["writes"]
    if not writes:
        return "unknown", "소유권 기록이 없어 수동 확인 전까지 보호합니다."
    if any(write["state"] == "Published" for write in writes):
        return "unknown", "게시된 기록과 현재 Dataset 참조가 불일치합니다."
    pending = [write for write in writes if write["state"] == "Pending"]
    if pending:
        if any(
            write["owner_kind"] == "attempt"
            and metadata["attempts"].get(write["owner_id"]) == "Running"
            for write in pending
        ):
            return "protected", "실행 중인 Attempt의 게시 의도가 있어 보호합니다."
        if any(
            write["owner_kind"] == "upload"
            and datetime.fromisoformat(write["updated_at"])
            > datetime.now(UTC) - timedelta(days=policy.grace_days)
            for write in pending
        ):
            return "protected", "완료되지 않은 최근 업로드가 있어 보호합니다."
        return "unknown", "완료 여부를 확인할 수 없는 오래된 게시 의도입니다. 삭제하지 않습니다."
    if any(write["state"] != "Failed" for write in writes):
        return "unknown", "종료 여부가 확실하지 않은 게시 시도가 존재합니다."
    latest = max(datetime.fromisoformat(write["updated_at"]) for write in writes)
    modified = datetime.fromisoformat(item["modified_at"])
    cutoff = datetime.now(UTC) - timedelta(days=policy.grace_days)
    if max(latest, modified) > cutoff:
        return "waiting", "미게시 객체의 보존 유예 기간이 지나지 않았습니다."
    observation = metadata["observation"]
    if not item.get("fingerprint") or observation["fingerprint"] != item["fingerprint"]:
        return "unknown", "객체 세대의 동일성을 확인하지 못해 보호합니다."
    first = datetime.fromisoformat(observation["first_seen_at"])
    if observation["observation_count"] < policy.min_observations or first > datetime.now(
        UTC
    ) - timedelta(hours=policy.observation_hours):
        return "waiting", "별도의 날짜에 반복 관측될 때까지 보존합니다."
    return "candidate", "실패 게시·경과 기간·반복 관측을 충족한 검토 후보입니다. 삭제하지 않습니다."


def inspect_page(
    store: Store,
    storage: ObjectStore,
    policy: StoragePolicy,
    cursor: str | None = None,
) -> dict:
    """한 접두사의 한 페이지만 조회한다. Iceberg는 절대 탐색하지 않는다."""
    prefix, token = _position(cursor)
    objects, next_token = storage.list_page(prefix, limit=policy.page_size, cursor=token)
    metadata = store.observe_objects(objects, policy.observation_hours)
    examined = []
    summary = {"protected": 0, "waiting": 0, "candidate": 0, "unknown": 0}
    for item in objects:
        status, reason = classify_object(item, metadata[item["location"]], policy)
        summary[status] += 1
        examined.append({**item, "status": status, "reason": reason})
    if next_token:
        next_cursor = _encode_position(prefix, next_token)
    elif prefix == "datasets/":
        next_cursor = _encode_position("results/", None)
    else:
        next_cursor = None
    return {
        "scope": prefix,
        "inspected_at": datetime.now(UTC).isoformat(),
        "items": examined,
        "counts": summary,
        "next_cursor": next_cursor,
        "policy": policy.describe(),
        "deletion_enabled": False,
    }


def inspect_iceberg_references(store: Store, limit: int = 50) -> list[dict]:
    """참조된 Snapshot만 조회한다. 테이블 메타데이터나 파일을 변경하지 않는다."""
    from .datasets import iceberg_catalog
    from .retention import snapshot_status

    with store.connection() as db:
        from sqlalchemy import text

        rows = (
            db.execute(
                text(
                    "SELECT id,name,location,version FROM datasets WHERE kind='iceberg' "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
    # 성공적으로 게시된 Dataset 외에 Tag만 남은 의도도 운영자가 발견할 수 있어야 한다.
    orphaned = store.unpublished_snapshot_retention(limit=limit)
    results = []
    catalog = None
    table_cache = {}
    for row in rows:
        try:
            if catalog is None:
                catalog = iceberg_catalog(store)
            if row["location"] not in table_cache:
                table_cache[row["location"]] = catalog.load_table(row["location"])
            status, reason = snapshot_status(store, dict(row), table_cache[row["location"]])
        except Exception:
            status = "unknown"
            reason = "Catalog 확인에 실패했습니다. 만료·삭제하지 마세요."
        results.append(
            {
                "dataset_id": row["id"],
                "table_name": row["location"],
                "snapshot_id": row["version"],
                "status": status,
                "reason": reason,
            }
        )
    from .retention import inspect_tag

    orphaned_results = []
    for item in orphaned:
        try:
            if catalog is None:
                catalog = iceberg_catalog(store)
            if item["location"] not in table_cache:
                table_cache[item["location"]] = catalog.load_table(item["location"])
            tag = inspect_tag(
                table_cache[item["location"]],
                name=item["tag_name"],
                snapshot_id=item["snapshot_id"],
            )
            reason = f"미게시 보존 의도({item['state']}): Catalog Tag={tag}. 자동 삭제·재시도하지 마세요."
        except Exception:
            reason = f"미게시 보존 의도({item['state']}): Catalog 확인 불가. 자동 삭제하지 마세요."
        orphaned_results.append(
            {
                "dataset_id": item["dataset_id"],
                "table_name": item["location"],
                "snapshot_id": item["snapshot_id"],
                "status": "unknown",
                "reason": reason,
            }
        )
    # 제한된 화면에서도 미확정 의도가 일반 게시 Dataset 목록에 가려지지 않도록 한다.
    return (orphaned_results + results)[:limit]
