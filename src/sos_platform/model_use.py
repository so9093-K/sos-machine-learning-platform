"""SOS가 수용한 모델 판본의 신규 사용 판정(ADR 0025).

Scoring·단일 입력 계산·온라인 추론·서빙 배포·제공은 모두 이 규칙으로 판정한다. 수명주기·판본 보관·
그룹 보관은 서로 다른 사실로 저장하고, 판정만 한 곳에서 한다. MLflow 등록·기록 상태는 외부 전송
결과이므로 판정에 쓰지 않는다. 판본은 게시 시점에 패키지·Runtime·SHA 검증을 마친 상태다.
"""

from collections.abc import Mapping

from sqlalchemy import Connection, text

DEFAULT_GROUP_TITLE = "기본 활동 이상 탐지"

# 판본 행에 그룹 보관 상태를 붙이는 조회 조각. 소유 Actor가 없는 이전 판본도 잃지 않도록 LEFT JOIN한다.
GROUP_JOIN = (
    "LEFT JOIN model_groups g ON g.owner_actor=m.owner_actor AND g.registry_key=m.registry_key"
)
GROUP_ARCHIVED = "g.archived_at AS group_archived_at"
USABLE_SQL = "m.lifecycle_state='Active' AND m.archived_at IS NULL AND g.archived_at IS NULL"


def use_blocked(model: Mapping) -> str | None:
    """새 사용을 막는 사유의 오류 코드. 사용할 수 있으면 None.

    Args:
        model: `lifecycle_state`·`archived_at`·`group_archived_at`을 포함한 판본 행.
    """
    if model.get("lifecycle_state") != "Active":
        return "SOS-ML-MODEL-INVALID"
    if model.get("archived_at") or model.get("group_archived_at"):
        return "SOS-ML-MODEL-ARCHIVED"
    return None


def locked_model(db: Connection, revision_id: str, *, for_update: bool = False) -> dict | None:
    """판본 행과 그 그룹 행을 잠그고 사용 판정에 필요한 상태를 읽는다.

    새 사용·제공·보관·삭제는 모두 판본 행을 먼저, 그룹 행을 다음에 잠근다. 그룹 보관은 그룹 행을
    배타적으로 잠근 뒤 그룹 안의 제공·서빙을 확인하므로, 판본 쪽 접수와 그룹 보관이 직렬화된다.
    """
    row = db.execute(text(
        "SELECT * FROM model_revisions WHERE id=:id " + ("FOR UPDATE" if for_update else "FOR SHARE")
    ), {"id": revision_id}).mappings().first()
    if row is None:
        return None
    group = db.execute(text(
        "SELECT archived_at FROM model_groups WHERE owner_actor=:owner AND registry_key=:key FOR SHARE"
    ), {"owner": row["owner_actor"], "key": row["registry_key"]}).scalar_one_or_none()
    return {**row, "group_archived_at": group}


def group_title(registry_key: str) -> str:
    """명시적으로 만들지 않은 그룹의 표시 이름."""
    return DEFAULT_GROUP_TITLE if registry_key == "default" else registry_key
