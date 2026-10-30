"""저장소 보존 정책·객체 점검과 Iceberg 참조 보존 API."""

from fastapi import APIRouter, Depends, HTTPException, Query

from .config import StoragePolicy
from .errors import PlatformError
from .object_store import ObjectStore
from .reconciler import inspect_iceberg_references, inspect_page
from .retention import ensure_tag, snapshot_status
from .schemas import (
    IcebergReferenceOut,
    StorageInspectionOut,
    StoragePolicyOut,
)
from .security import Actor, require
from .store import Store

# Depends 객체는 요청마다 재생성하지 않고 안정적으로 재사용한다.
INSPECT_DEP = Depends(require("storage.inspect"))
RETAIN_DEP = Depends(require("snapshot.retain"))


def storage_router(store: Store) -> APIRouter:
    """운영 권한의 저장소 점검·보존 경로. 객체를 삭제하지 않는다."""
    router = APIRouter()

    @router.get(
        "/api/storage/policy",
        tags=["Storage"],
        summary="저장소 보존 정책 조회",
        response_model=StoragePolicyOut,
    )
    def storage_policy(actor: Actor = INSPECT_DEP):
        """연결 비밀을 제외한 유효 정책과 출처를 확인한다."""
        return StoragePolicy.from_env().describe()

    @router.get(
        "/api/storage/inspection",
        tags=["Storage"],
        summary="저장소 객체 점검 결과 조회",
        description="저장소의 객체 상태를 점검합니다. 점검은 실제 객체 삭제를 수행하지 않습니다.",
        response_model=StorageInspectionOut,
    )
    def storage_inspection(
        cursor: str | None = Query(default=None, max_length=4096),
        actor: Actor = INSPECT_DEP,
    ):
        """S3 객체를 읽기 전용으로 대조한다. 실제 삭제는 제공하지 않는다."""
        try:
            return inspect_page(store, ObjectStore(), StoragePolicy.from_env(), cursor)
        except ValueError:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="저장소 객체 목록을 조회할 수 없습니다. 객체는 삭제되지 않았습니다."
            ) from exc

    @router.get(
        "/api/storage/iceberg-references",
        tags=["Iceberg"],
        summary="Iceberg 참조 보존 상태 조회",
        response_model=list[IcebergReferenceOut],
    )
    def storage_iceberg_references(
        limit: int = Query(default=50, ge=1, le=100),
        actor: Actor = INSPECT_DEP,
    ):
        """등록된 Dataset이 참조하는 Iceberg Snapshot 존재 여부를 확인한다."""
        return inspect_iceberg_references(store, limit=limit)

    @router.post(
        "/api/storage/iceberg-references/{dataset_id}/retain",
        tags=["Iceberg"],
        summary="Iceberg 참조 보존 요청",
    )
    def retain_iceberg_reference(dataset_id: str, actor: Actor = RETAIN_DEP):
        """기존 Dataset을 재확인하고 SOS 영구 보존 Tag를 생성한다."""
        item = store.get_dataset(dataset_id)
        if item is None or item["kind"] != "iceberg":
            raise HTTPException(status_code=404, detail="Iceberg Dataset을 찾을 수 없습니다.")
        try:
            ensure_tag(
                store,
                dataset_id=dataset_id,
                location=item["location"],
                snapshot_id=item["version"],
            )
            store.confirm_existing_retention(dataset_id, actor_id=actor.id, actor_type=actor.kind)
        except Exception as exc:
            # 외부 Catalog에 Tag 쓰기가 이미 성공했을 수 있다. 원문을 공개하거나
            # 안전한 재시도를 암시하지 않고 수동 확인이 필요한 상태로 표현한다.
            raise PlatformError("SOS-ICEBERG-RETAIN") from exc
        status, reason = snapshot_status(store, item)
        return {"dataset_id": dataset_id, "status": status, "reason": reason}

    return router
