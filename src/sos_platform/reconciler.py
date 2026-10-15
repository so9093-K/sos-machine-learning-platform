"""S3 객체 목록과 PostgreSQL 게시·소유권 기록을 안전하게 대조한다.

이 모듈은 버킷 객체를 삭제하지 않는다. 현재 API에서 반환하는 후보는
자동 삭제 허가가 아니며, DB 또는 S3를 검사할 수 없으면 진단은 실패한다.
"""

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta

from .config import StoragePolicy
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
        raise ValueError("Invalid inventory cursor") from exc
    if (
        not isinstance(position, dict)
        or set(position) != {"prefix", "token"}
        or position["prefix"] not in PREFIXES
        or (
            position["token"] is not None
            and (not isinstance(position["token"], str) or not position["token"])
        )
    ):
        raise ValueError("Invalid inventory cursor")
    return position["prefix"], position["token"]


def _encode_position(prefix: str, token: str | None) -> str:
    payload = json.dumps({"prefix": prefix, "token": token}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def classify_object(item: dict, metadata: dict, policy: StoragePolicy) -> tuple[str, str]:
    """S3 데이터의 삭제 가능성이 아니라 관측 시점의 보호·검토 상태를 반환한다."""
    if metadata["referenced"]:
        return "protected", "Dataset이 이 객체를 참조하고 있습니다."
    writes = metadata["writes"]
    if not writes:
        return "unknown", "소유권 기록이 없어 수동 확인 전까지 보호합니다."
    if any(write["state"] == "Published" for write in writes):
        return "unknown", "게시된 기록과 현재 Dataset 참조가 불일치합니다."
    if any(
        write["state"] == "Pending"
        and (
            write["owner_kind"] == "upload"
            or metadata["attempts"].get(write["owner_id"]) == "Running"
        )
        for write in writes
    ):
        return "protected", "게시 또는 실행 중인 소유자가 있을 수 있습니다."
    if any(write["state"] != "Failed" for write in writes):
        return "unknown", "종료 여부가 확실하지 않은 게시 시도가 존재합니다."
    latest = max(datetime.fromisoformat(write["updated_at"]) for write in writes)
    modified = datetime.fromisoformat(item["modified_at"])
    cutoff = datetime.now(UTC) - timedelta(days=policy.grace_days)
    if max(latest, modified) > cutoff:
        return "waiting", "미게시 객체의 보존 유예 기간이 지나지 않았습니다."
    observation = metadata["observation"]
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
    metadata = store.observe_objects([obj["location"] for obj in objects], policy.observation_hours)
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
        "items": examined,
        "counts": summary,
        "next_cursor": next_cursor,
        "policy": policy.describe(),
        "deletion_enabled": False,
    }


def inspect_iceberg_references(store: Store, limit: int = 50) -> list[dict]:
    """참조된 Snapshot만 조회한다. 테이블 메타데이터나 파일을 변경하지 않는다."""
    from .datasets import iceberg_catalog

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
    results = []
    catalog = None
    for row in rows:
        try:
            if catalog is None:
                catalog = iceberg_catalog(store)
            table = catalog.load_table(row["location"])
            available = str(row["version"]) in {str(s.snapshot_id) for s in table.snapshots()}
            status = "protected" if available else "missing"
            reason = (
                "고정 Snapshot이 Catalog에 존재합니다."
                if available
                else "고정 Snapshot이 누락됐습니다. 재현성을 확인하세요."
            )
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
    return results
