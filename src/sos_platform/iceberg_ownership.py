"""기존 Iceberg 테이블의 명시적·비파괴적 쓰기 소유권 채택.

마이그레이션은 스키마만 변경한다. Catalog 쓰기 소유권은 로컬 관리자
의사의 명시적 확인 없이는 어떤 기존 Dataset으로부터도 자동 이전하지 않는다.
"""

import argparse
import json
import os
import re
from pathlib import Path
from uuid import UUID

from pyiceberg.exceptions import NoSuchTableError

from .datasets import iceberg_catalog
from .store import Store

TABLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


def inspect_legacy_table(store: Store, table_name: str) -> dict:
    """쓰기 없이 Catalog 신원과 기존 Dataset 소유권 충돌을 조사한다."""
    if not TABLE_NAME.fullmatch(table_name):
        raise ValueError("Invalid Iceberg table name")
    catalog = iceberg_catalog(store)
    try:
        table = catalog.load_table(f"default.{table_name}")
    except NoSuchTableError:
        return {"table": f"default.{table_name}", "state": "missing"}
    current = table.current_snapshot()
    evidence = store.iceberg_ownership_evidence(table_name)
    claim = store.iceberg_write_claim(table_name)
    property_token = table.metadata.properties.get("sos.write-token")
    if claim and claim["state"] == "Active" and property_token == claim["token"]:
        state = "active"
    elif (
        claim
        and claim["state"] == "Adopting"
        and property_token
        in (
            None,
            claim["token"],
        )
    ):
        state = "resume_required"
    elif claim or property_token is not None:
        state = "conflict"
    elif current is None or not evidence["total"]:
        state = "insufficient_evidence"
    elif evidence["anonymous"] or evidence["owners"] != 1:
        state = "ambiguous_owner"
    else:
        state = "candidate"
    return {
        "table": f"default.{table_name}",
        "table_uuid": str(table.metadata.table_uuid),
        "current_snapshot_id": str(current.snapshot_id) if current else None,
        "dataset_references": evidence["total"],
        "owner_candidate": evidence["candidate"] if evidence["owners"] == 1 else None,
        "state": state,
    }


def adopt_legacy_table(
    store: Store,
    *,
    table_name: str,
    owner_actor: str,
    dataset_id: str,
    expected_table_uuid: str,
    expected_snapshot_id: str,
) -> dict:
    """사용자가 확인한 Table UUID·Snapshot·Dataset 소유자를 대조해 채택한다.

    PostgreSQL 예약과 Catalog Metadata 커밋은 분산 트랜잭션이 아니다.
    중간 실패 시 Adopting 상태를 유지하고 같은 인자로 안전하게 재진입한다.
    """
    UUID(expected_table_uuid)
    if not expected_snapshot_id.isdigit() or not owner_actor:
        raise ValueError("Expected snapshot and owner are required")
    report = inspect_legacy_table(store, table_name)
    if report["state"] not in {"candidate", "resume_required"}:
        raise PermissionError("Legacy Iceberg table cannot be adopted")
    if (
        report["table_uuid"] != expected_table_uuid
        or report["current_snapshot_id"] != expected_snapshot_id
        or report["owner_candidate"] != owner_actor
    ):
        raise PermissionError("Catalog identity or owner changed; inspect again")
    dataset = store.get_dataset(dataset_id)
    if (
        dataset is None
        or dataset["kind"] != "iceberg"
        or dataset["location"] != report["table"]
        or dataset["owner_actor"] != owner_actor
    ):
        raise PermissionError("Selected Dataset does not prove table reference ownership")
    catalog = iceberg_catalog(store)
    table = catalog.load_table(report["table"])
    if dataset["version"] not in {str(s.snapshot_id) for s in table.snapshots()}:
        raise PermissionError("Selected Dataset snapshot no longer exists")
    claim = store.begin_legacy_iceberg_adoption(table_name, owner_actor, dataset_id)
    # API/Worker는 token 없는 기존 테이블에는 쓰지 못한다.
    # 예약 이후에도 테이블이 교체되거나 Snapshot이 진행됐다면 자동 재시도하지 않는다.
    table = catalog.load_table(report["table"])
    current = table.current_snapshot()
    if (
        str(table.metadata.table_uuid) != expected_table_uuid
        or current is None
        or str(current.snapshot_id) != expected_snapshot_id
    ):
        raise PermissionError("Catalog identity changed during adoption")
    property_token = table.metadata.properties.get("sos.write-token")
    if property_token not in (None, claim["token"]):
        raise PermissionError("An unrelated Catalog write owner already exists")
    if property_token is None:
        table.transaction().set_properties({"sos.write-token": claim["token"]}).commit_transaction()
    # Catalog 응답 유실 시 이 확인이 실행되지 않을 수 있다.
    # 재시행하면 기존 같은 토큰만 허용하며 새 소유권을 만들지 않는다.
    table = catalog.load_table(report["table"])
    current = table.current_snapshot()
    if (
        table.metadata.properties.get("sos.write-token") != claim["token"]
        or str(table.metadata.table_uuid) != expected_table_uuid
        or current is None
        or str(current.snapshot_id) != expected_snapshot_id
    ):
        raise PermissionError("Catalog adoption has an uncertain outcome; inspect again")
    store.complete_legacy_iceberg_adoption(table_name, owner_actor, claim["token"])
    return inspect_legacy_table(store, table_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="기존 Iceberg 테이블 소유권 진단·채택")
    parser.add_argument("--table", required=True, help="default 네임스페이스의 테이블명")
    parser.add_argument("--owner")
    parser.add_argument("--dataset-id")
    parser.add_argument("--expect-snapshot")
    parser.add_argument("--expect-table-uuid")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = Store(Path(os.environ.get("SOS_HOME", ".sos")))
    try:
        store.initialize()
        if args.apply:
            if not all((args.owner, args.dataset_id, args.expect_snapshot, args.expect_table_uuid)):
                parser.error(
                    "--apply requires --owner, --dataset-id, --expect-snapshot and --expect-table-uuid"
                )
            result = adopt_legacy_table(
                store,
                table_name=args.table,
                owner_actor=args.owner,
                dataset_id=args.dataset_id,
                expected_snapshot_id=args.expect_snapshot,
                expected_table_uuid=args.expect_table_uuid,
            )
        else:
            result = inspect_legacy_table(store, args.table)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (PermissionError, ValueError) as exc:
        print(json.dumps({"state": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 3
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
