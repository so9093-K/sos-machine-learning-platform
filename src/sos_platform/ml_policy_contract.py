"""판정 정책을 다른 Scoring Result에 재사용할 수 있는지 판단하는 계약.

API 제출 검사와 Worker 평가가 같은 규칙을 사용하므로 ML 계산 라이브러리에 의존하지 않는다.
"""


def validate_policy_for_scoring(
    store,
    policy: dict,
    target: dict,
    *,
    expected_policy_owner: str | None = None,
    expected_reference_owner: str | None = None,
) -> dict:
    """정책 보정 근거와 평가 입력의 모델·점수 의미가 같은지 검사한다.

    정책은 보정 Scoring Result를 영구 참조하며 새 평가 대상에 재사용할 수 있다.
    다른 Scoring Result라고 해서 관측 행이 서로 겹치지 않는다는 보장은 없다.

    Args:
        store: 판본 조회를 제공하는 SOS Store.
        policy: 사용 권한을 확인한 불변 Policy Revision.
        target: 평가 대상 Scoring Result.
        expected_policy_owner: Job 제출 시 고정한 정책 소유자.
        expected_reference_owner: Job 제출 시 고정한 보정 Scoring 소유자.

    Returns:
        dict: 보정 근거인 Scoring Result.

    Raises:
        ValueError: 보정 근거·소유자·Model Revision·점수 계약 불일치.
    """
    if expected_policy_owner is not None and policy["owner_actor"] != expected_policy_owner:
        raise ValueError("Decision Policy owner changed")
    reference = store.get_scoring_result(policy["scoring_result_id"])
    if reference is None:
        raise ValueError("Decision Policy calibration source is unavailable")
    if expected_reference_owner is not None and reference["owner_actor"] != expected_reference_owner:
        raise ValueError("Decision Policy calibration owner changed")
    if reference["model_revision_id"] != target["model_revision_id"]:
        raise ValueError("Decision Policy requires the same Model Revision")
    if reference["score_direction"] != target["score_direction"]:
        raise ValueError("Decision Policy score direction differs from target")
    if reference["feature_names"] != target["feature_names"]:
        raise ValueError("Decision Policy feature signature differs from target")
    if reference["score_summary"].get("score_scale") != target["score_summary"].get("score_scale"):
        raise ValueError("Decision Policy score scale differs from target")
    return reference
