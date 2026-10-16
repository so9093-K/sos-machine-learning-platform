"""불변 Dataset의 기계적 특성과 실행 가능한 탐색 제안을 생성한다.

업무적 의미는 확정하지 않으며 원시 범주 값과 민감한 예시는 결과에 포함하지 않는다.
"""

import re

from .discovery import (
    _columns,
    _numeric_type,
    _profile_number,
    _records,
    quoted,
    source_connection,
)
from .store import Store

ALGORITHM_VERSION = "understanding-v1"
SAMPLE_ROWS = 1000
SAMPLE_SEED = 42


def role_candidates(name: str, dtype: str, unique: int, total: int) -> list[dict]:
    """자료형·표본 통계·약한 이름 힌트로 확인이 필요한 역할 후보를 제시한다."""
    kind = dtype.upper()
    values: list[dict] = []

    def add(role: str, basis: str) -> None:
        if not any(item["role"] == role for item in values):
            values.append({"role": role, "basis": basis, "confirmed": False})

    if any(token in kind for token in ("DATE", "TIME")):
        add("temporal", "physical_dtype")
    elif re.search(r"(?:^|_)(?:ts|timestamp|time|date|datetime)(?:$|_)", name, re.I):
        add("temporal", "column_name_hint")
    if re.search(r"(?:^|_)(?:id|uuid|key|hash|ip|user_name)(?:$|_)", name, re.I):
        add("identifier", "column_name_hint")
    if "BOOL" in kind:
        add("categorical", "physical_dtype")
    elif total and 1 < unique <= min(32, max(2, total // 2)):
        add("categorical", "sample_cardinality")
    if (
        any(token in kind for token in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "HUGE"))
        and not any(item["role"] == "identifier" for item in values)
    ):
        add("numeric_measure", "physical_dtype")
    if re.search(r"(?:^|_)(?:ip|email|phone|user|name|ssn|token)(?:$|_)", name, re.I):
        add("potentially_sensitive", "column_name_hint")
    return values


def build_understanding(store: Store, dataset: dict, *, scope: str = "sample") -> dict:
    """한 번의 제한된 DuckDB 집계로 원본 판본의 안전한 프로파일을 계산한다.

    표본은 고정 seed의 Reservoir 표본이며 전체 집계의 고유값은 근사치다.
    호출자가 전체 스캔의 Worker 예산을 적용해야 한다.
    """
    if scope not in ("sample", "full"):
        raise ValueError("Unsupported understanding scope")
    with source_connection(store, dataset) as conn:
        schema = _columns(conn)
        if not schema:
            return {
                "dataset_id": dataset["id"], "version": dataset["version"],
                "algorithm_version": ALGORITHM_VERSION, "scope": scope,
                "sampling_method": "none", "sample_limit": None,
                "row_count": 0, "columns": [], "insights": [],
            }
        relation = (
            "source"
            if scope == "full"
            else "(SELECT * FROM source USING SAMPLE reservoir("
            + str(SAMPLE_ROWS) + " ROWS) REPEATABLE (" + str(SAMPLE_SEED) + "))"
        )
        expressions = ["COUNT(*) AS __row_count"]
        for n, item in enumerate(schema):
            col = quoted(item["name"])
            key = "c" + str(n)
            expressions.append("COUNT(*) - COUNT(" + col + ") AS " + key + "_null")
            distinct = "COUNT(DISTINCT " + col + ")" if scope == "sample" else (
                "APPROX_COUNT_DISTINCT(" + col + ")"
            )
            expressions.append(distinct + " AS " + key + "_unique")
            sql_type = item["sql_type"].upper()
            if sql_type in ("FLOAT", "DOUBLE", "REAL"):
                expressions.append(
                    "COUNT(*) FILTER (WHERE isnan(" + col + ")) AS " + key + "_nan"
                )
            if _numeric_type(sql_type):
                valid = (
                    "CASE WHEN isfinite(" + col + ") THEN " + col + " ELSE NULL END"
                    if sql_type in ("FLOAT", "DOUBLE", "REAL") else col
                )
                for metric in ("MIN", "MAX", "AVG"):
                    expressions.append(
                        metric + "(" + valid + ") AS " + key + "_" + metric.lower()
                    )
        values = _records(conn.execute(
            "SELECT " + ", ".join(expressions) + " FROM " + relation
        ))[0]
    count = int(values["__row_count"])
    columns: list[dict] = []
    for n, item in enumerate(schema):
        key = "c" + str(n)
        unique = int(values[key + "_unique"] or 0)
        nulls = int(values[key + "_null"] or 0)
        col = {
            "name": item["name"], "dtype": item["dtype"],
            "null_count": nulls, "null_ratio": nulls / count if count else None,
            "nan_count": int(values[key + "_nan"]) if key + "_nan" in values else None,
            "distinct_count": unique,
            "distinct_exact": scope == "sample",
            "roles": role_candidates(item["name"], item["dtype"], unique, count),
            "is_constant": count > 0 and unique <= 1 and nulls == 0,
        }
        for metric in ("min", "max", "avg"):
            if key + "_" + metric in values:
                col["mean" if metric == "avg" else metric] = _profile_number(
                    values[key + "_" + metric]
                )
        columns.append(col)
    insights = suggest_analyses(columns, count)
    return {
        "dataset_id": dataset["id"], "version": dataset["version"],
        "algorithm_version": ALGORITHM_VERSION, "scope": scope,
        "sampling_method": "full_scan" if scope == "full" else "reservoir",
        "sample_limit": None if scope == "full" else SAMPLE_ROWS,
        "sample_seed": None if scope == "full" else SAMPLE_SEED,
        "row_count": count, "columns": columns, "insights": insights,
    }


def suggest_analyses(columns: list[dict], row_count: int) -> list[dict]:
    """기존 Exploration API가 실행할 수 있는 요청만 제안한다."""
    if not row_count:
        return []
    suggestions: list[dict] = []
    numeric = next((
        col for col in columns
        if any(r["role"] == "numeric_measure" for r in col["roles"])
        and not col["is_constant"] and col["distinct_count"] > 1
        and col["null_count"] < row_count
    ), None)
    categories = [
        col for col in columns
        if any(r["role"] == "categorical" for r in col["roles"])
        and not any(r["role"] == "potentially_sensitive" for r in col["roles"])
        and not col["is_constant"] and col["distinct_count"] > 1
    ]
    missing = next((col for col in columns if col["null_count"] > 0), None)

    def append(kind: str, title: str, reason: str, column: str, metric: str,
               group_by: str | None = None) -> None:
        suggestions.append({
            "kind": kind, "title": title, "reason": reason,
            "scope": "sample", "requires_confirmation": False,
            "request": {"column": column, "metric": metric, "group_by": group_by},
        })

    if numeric and categories:
        append("group_comparison", "그룹별 수치 비교",
               "범주형 후보와 변동이 있는 숫자형 컬럼이 발견됐습니다.",
               numeric["name"], "mean", categories[0]["name"])
    if numeric:
        append("numeric_summary", "숫자형 분포 확인",
               "변동이 있는 숫자형 컬럼이 발견됐습니다.",
               numeric["name"], "max")
    if categories:
        append("category_count", "그룹별 행 수 비교",
               "서로 다른 값이 반복되는 범주형 컬럼이 발견됐습니다.",
               categories[0]["name"], "count", categories[0]["name"])
    if missing:
        append("missingness", "결측 현황 확인",
               "값이 비어 있는 컬럼이 발견됐습니다.",
               missing["name"], "null_count")
    return suggestions[:4]
