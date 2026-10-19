"""Dataset의 역할·탐색 추천 로직은 인프라 없이 검증한다."""

from sos_platform.understanding import role_candidates, suggest_analyses



def test_role_inference_never_confirms_meaning():
    roles = role_candidates("ts", "String", 10, 10)
    assert any(role["role"] == "temporal" for role in roles)
    assert all(role["confirmed"] is False for role in roles)
    assert any(
        role["role"] == "potentially_sensitive"
        for role in role_candidates("user_name", "String", 10, 10)
    )
    for name in ("password_hash", "access_token", "client_secret", "api_key", "host_ip"):
        assert any(
            role["role"] == "potentially_sensitive"
            for role in role_candidates(name, "String", 2, 10)
        )



def test_suggestions_only_emit_supported_operations():
    columns = [
        {
            "name": "group",
            "roles": [{"role": "categorical"}],
            "is_constant": False,
            "distinct_count": 2,
            "null_count": 0,
        },
        {
            "name": "value",
            "roles": [{"role": "numeric_measure"}],
            "is_constant": False,
            "distinct_count": 4,
            "null_count": 0,
        },
    ]
    proposals = suggest_analyses(columns, 10)
    assert proposals
    assert all(
        p["request"]["metric"] in {"count", "null_count", "mean", "min", "max"} for p in proposals
    )
    assert proposals[0]["request"]["group_by"] == "group"
    assert suggest_analyses([], 0) == []
    # 하나의 숫자형 범주를 자기 자신으로 묶어 평균을 계산하는 후보를 피한다.
    score = {
        "name": "unnamed_1",
        "roles": [{"role": "numeric_measure"}, {"role": "categorical"}],
        "is_constant": False,
        "distinct_count": 4,
        "null_count": 0,
    }
    self_grouped = suggest_analyses([score], 10)
    assert all(
        item["request"].get("group_by") != "unnamed_1" or item["request"]["metric"] == "count"
        for item in self_grouped
    )

    # 식별자나 비밀값으로 추론한 범주 컬럼은 자동 그룹 집계로 권장하지 않는다.
    secret_group = {
        "name": "password_hash",
        "roles": [
            {"role": "categorical"},
            {"role": "identifier"},
            {"role": "potentially_sensitive"},
        ],
        "is_constant": False,
        "distinct_count": 2,
        "null_count": 0,
    }
    restricted = suggest_analyses([secret_group, columns[1]], 10)
    assert all(p["request"]["group_by"] != "password_hash" for p in restricted)

    # CSV/Parquet 컬럼명은 API ExplorationSpec의 길이 제한보다 길 수 있다.
    # 그런 컬럼에서 실행 불가능한 추천을 만들지 않아야 한다.
    too_long = {
        "name": "x" * 257,
        "roles": [{"role": "numeric_measure"}],
        "is_constant": False,
        "distinct_count": 5,
        "null_count": 0,
    }
    assert suggest_analyses([too_long], 10) == []
