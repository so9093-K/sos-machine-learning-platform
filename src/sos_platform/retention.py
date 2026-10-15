"""Iceberg Snapshot 보호 계약을 Catalog 구현과 분리한다.

하나의 Dataset에 고유한 영구 Tag를 사용한다. 실제 삭제·Tag 자동 해제는
구현하지 않으며 Catalog 쓰기 결과가 불확실할 때는 보호 성공으로 표시하지 않는다.
"""

from uuid import UUID

from .store import Store


def tag_name(dataset_id: str) -> str:
    """Dataset 식별자에서 충돌 없는 소유 Tag 이름을 만든다."""
    return f"sos_dataset_{UUID(dataset_id).hex}"


def inspect_tag(table, *, name: str, snapshot_id: str) -> str:
    """참조 존재·유형·대상·유효 수명을 모두 확인한다."""
    snapshot = str(snapshot_id)
    if snapshot not in {str(s.snapshot_id) for s in table.snapshots()}:
        return "missing"
    reference = table.metadata.refs.get(name)
    if reference is None:
        return "available_unprotected"
    ref_type = getattr(reference.snapshot_ref_type, "value", reference.snapshot_ref_type)
    if (
        str(reference.snapshot_id) == snapshot
        and str(ref_type).lower() == "tag"
        and reference.max_ref_age_ms is None
    ):
        return "retained"
    return "available_unprotected"


def ensure_tag(store: Store, *, dataset_id: str, location: str, snapshot_id: str) -> str:
    """보존 의도 기록 후 Tag를 생성·재확인한다. 실패 시 즉시 삭제하지 않는다."""
    from .datasets import iceberg_catalog

    name = tag_name(dataset_id)
    store.begin_snapshot_retention(
        dataset_id=dataset_id,
        location=location,
        snapshot_id=str(snapshot_id),
        tag_name=name,
    )
    try:
        catalog = iceberg_catalog(store)
        table = catalog.load_table(location)
        status = inspect_tag(table, name=name, snapshot_id=str(snapshot_id))
        if status == "missing":
            raise ValueError("Iceberg Snapshot is no longer available")
        if status == "available_unprotected":
            if name in table.metadata.refs:
                raise ValueError("Snapshot tag conflicts with another retention target")
            table.manage_snapshots().create_tag(int(snapshot_id), name).commit()
            table = catalog.load_table(location)
        if inspect_tag(table, name=name, snapshot_id=str(snapshot_id)) != "retained":
            raise RuntimeError("Snapshot retention could not be verified")
    except Exception as exc:
        try:
            store.fail_snapshot_retention(dataset_id, str(exc))
        except Exception:
            # 외부 Catalog 커밋 성공 여부를 확인하지 못했다면 Tag를 해제하지 않는다.
            pass
        raise
    return dataset_id


def snapshot_status(store: Store, dataset: dict, table=None) -> tuple[str, str]:
    """Catalog의 실상과 DB 보존 의도를 함께 검사한다."""
    from .datasets import iceberg_catalog

    record = store.snapshot_retention(dataset["id"])
    try:
        table = table or iceberg_catalog(store).load_table(dataset["location"])
        if str(dataset["version"]) not in {str(s.snapshot_id) for s in table.snapshots()}:
            return "missing", "Dataset에 연결된 Snapshot이 없어 재현할 수 없습니다."
        if not record:
            return "available_unprotected", "현재 읽을 수 있지만 SOS 보호 Tag가 없습니다."
        status = inspect_tag(table, name=record["tag_name"], snapshot_id=str(dataset["version"]))
        if status == "retained" and record["state"] == "Retained":
            return "retained", "고정 Snapshot과 만료 기한 없는 SOS 보호 Tag를 확인했습니다."
        if status == "retained":
            return "unknown", "Tag는 존재하나 Dataset 게시·보존 상태를 확인해야 합니다."
        return "available_unprotected", "Snapshot은 존재하지만 유효한 보존 Tag가 없습니다."
    except Exception:
        return "unknown", "Catalog 확인이 불가능합니다. 보존을 보장할 수 없습니다."
