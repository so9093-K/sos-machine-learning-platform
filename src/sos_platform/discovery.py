"""불변 Dataset을 대상으로 DuckDB 탐색·통계 연산을 수행한다.

외부 SQL과 경로를 받지 않는다. 요청 컬럼은 실제 스키마에서 확인하며
Dataset 판본과 접근 권한의 판단은 호출자인 API·Worker가 담당한다.
"""

import base64
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from tempfile import TemporaryDirectory

import duckdb
import pyarrow.parquet as pq

from .datasets import iceberg_catalog, local_dataset_file
from .errors import PlatformError
from .store import Store


@dataclass(frozen=True)
class DiscoveryPolicy:
    """탐색 실행의 단일 연결당 자원 예산."""

    memory_mib: int = 1024
    threads: int = 2
    temp_mib: int = 2048
    max_groups: int = 100
    max_sample: int = 10000

    @classmethod
    def from_env(cls) -> "DiscoveryPolicy":
        names = {
            "memory_mib": ("SOS_DISCOVERY_MEMORY_MIB", 128, 16384, 1024),
            "threads": ("SOS_DISCOVERY_THREADS", 1, 16, 2),
            "temp_mib": ("SOS_DISCOVERY_TEMP_MIB", 128, 65536, 2048),
            "max_groups": ("SOS_DISCOVERY_MAX_GROUPS", 1, 1000, 100),
            "max_sample": ("SOS_DISCOVERY_MAX_SAMPLE", 100, 100000, 10000),
        }
        values = {}
        for field, (name, low, high, default) in names.items():
            raw = os.getenv(name, str(default))
            if not raw.isdecimal() or not low <= int(raw) <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
            values[field] = int(raw)
        return cls(**values)


def quoted(name: str) -> str:
    """이미 검증된 식별자를 SQL 식별자 형태로 직렬화한다."""
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


MAX_SAFE_JSON_INTEGER = 2**53 - 1


_NUMERIC_SQL_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
}


def _numeric_type(sql_type: str) -> bool:
    kind = sql_type.upper()
    return kind in _NUMERIC_SQL_TYPES or kind.startswith("DECIMAL")


def _dtype(dtype: str) -> str:
    """원본 SQL 타입의 크기·부호·시간대 의미를 보존해 UI에 노출한다."""
    kind = dtype.upper()
    integer = {
        "TINYINT": "Int8",
        "SMALLINT": "Int16",
        "INTEGER": "Int32",
        "BIGINT": "Int64",
        "HUGEINT": "Int128",
        "UTINYINT": "UInt8",
        "USMALLINT": "UInt16",
        "UINTEGER": "UInt32",
        "UBIGINT": "UInt64",
        "UHUGEINT": "UInt128",
    }
    if kind in integer:
        return integer[kind]
    if kind in ("FLOAT", "REAL"):
        return "Float32"
    if kind == "DOUBLE":
        return "Float64"
    if kind.startswith("DECIMAL"):
        return kind.title()
    if kind in ("VARCHAR", "CHAR", "TEXT"):
        return "String"
    if kind in ("BOOLEAN", "BOOL"):
        return "Boolean"
    if kind == "DATE":
        return "Date"
    if kind in ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"):
        return "Datetime(time_unit='us', time_zone='UTC')"
    units = {"TIMESTAMP_S": "s", "TIMESTAMP_MS": "ms", "TIMESTAMP_NS": "ns"}
    if kind in units:
        return f"Datetime(time_unit='{units[kind]}', time_zone=None)"
    if kind == "TIMESTAMP":
        return "Datetime(time_unit='us', time_zone=None)"
    return dtype


def _json_value(value):
    """Preview/그룹 키에서 정밀도·비유한 수·바이너리·시간 의미를 보존한다.

    큰 정수와 DECIMAL은 JSON 숫자의 정밀도 손실을 피하기 위해 문자열로,
    임의 bytes는 `base64:` 접두 문자열로 인코딩한다. 원본 dtype이 해석 기준이다.
    """
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value) if abs(value) > MAX_SAFE_JSON_INTEGER else value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "base64:" + base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _profile_number(value):
    """비유한 통계는 NULL로, 정밀도 손실 가능한 수치는 문자열로 노출한다."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return _json_value(value)


def _aggregate_number(value):
    """미정의된 비유한 집계값은 NULL로 반환하며 유효한 정밀 수치는 보존한다."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return _json_value(value)


def _records(cursor) -> list[dict]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


@dataclass(frozen=True)
class DiscoveryAdmission:
    """모든 API 인스턴스와 전체 Worker를 위한 DB 기준 탐색 슬롯."""

    max_active: int = 1
    wait_seconds: int = 15

    @classmethod
    def from_env(cls) -> "DiscoveryAdmission":
        active = os.getenv("SOS_DISCOVERY_MAX_ACTIVE", "1")
        wait = os.getenv("SOS_DISCOVERY_WAIT_SECONDS", "15")
        if not active.isdecimal() or not 1 <= int(active) <= 8:
            raise ValueError("SOS_DISCOVERY_MAX_ACTIVE must be between 1 and 8")
        if not wait.isdecimal() or not 1 <= int(wait) <= 300:
            raise ValueError("SOS_DISCOVERY_WAIT_SECONDS must be between 1 and 300")
        return cls(max_active=int(active), wait_seconds=int(wait))


@contextmanager
def discovery_slot(store: Store, policy: DiscoveryAdmission | None = None):
    """PostgreSQL 세션 advisory lock으로 원격 API 인스턴스 간 실행 수를 제한한다."""
    budget = policy or DiscoveryAdmission.from_env()
    deadline = time.monotonic() + budget.wait_seconds
    with store.engine.connect() as gate:
        acquired = None
        while acquired is None:
            for slot in range(budget.max_active):
                locked = gate.execute(
                    text("SELECT pg_try_advisory_lock(83417, :slot)"), {"slot": slot}
                ).scalar_one()
                gate.commit()
                if locked:
                    acquired = slot
                    break
            if acquired is not None:
                break
            if time.monotonic() >= deadline:
                raise PlatformError("SOS-DISCOVERY-CAPACITY")
            time.sleep(0.05)
        try:
            yield
        finally:
            gate.execute(text("SELECT pg_advisory_unlock(83417, :slot)"), {"slot": acquired})
            gate.commit()


@contextmanager
def source_connection(
    store: Store, dataset: dict, policy: DiscoveryPolicy | None = None
) -> Iterator[duckdb.DuckDBPyConnection]:
    """작업마다 새로운 DuckDB 연결을 만들고 허용한 원본만 스캔한다.

    Iceberg는 PyIceberg가 지정 Snapshot과 삭제 파일을 해석한 Arrow 배치로 연결한다.
    """
    policy = policy or DiscoveryPolicy.from_env()
    scratch_root = store.home / "cache" / "discovery-tmp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    # 각 요청에만 속한 Spill 파일은 연결 종료 후 함께 정리한다.
    # 다른 사용자·Worker가 사용하는 경로나 기존 캐시를 삭제하지 않는다.
    with discovery_slot(store):
        with TemporaryDirectory(prefix="query-", dir=scratch_root) as directory:
            scratch = Path(directory)
            path = local_dataset_file(store, dataset) if dataset["kind"] == "file" else None
            connection = duckdb.connect(
                database=":memory:",
                config={
                    "memory_limit": f"{policy.memory_mib}MiB",
                    "threads": str(policy.threads),
                    "temp_directory": str(scratch),
                    "max_temp_directory_size": f"{policy.temp_mib}MiB",
                    "allow_community_extensions": "false",
                    "autoinstall_known_extensions": "false",
                    "autoload_known_extensions": "false",
                    "allow_unsigned_extensions": "false",
                },
            )
            reader = None
            try:
                if path is not None:
                    connection.execute(f"SET allowed_paths = [{_literal(str(path))}]")
                    connection.execute(f"SET allowed_directories = [{_literal(str(scratch))}]")
                    connection.execute("SET enable_external_access = false")
                    function = "read_csv" if path.suffix.lower() == ".csv" else "read_parquet"
                    connection.execute(
                        f"CREATE TEMP VIEW source AS SELECT * FROM {function}({_literal(str(path))})"
                    )
                elif dataset["kind"] == "iceberg":
                    table = iceberg_catalog(store).load_table(dataset["location"])
                    snapshot = int(dataset["version"])
                    if snapshot not in {s.snapshot_id for s in table.snapshots()}:
                        raise ValueError("Iceberg snapshot is not available; it may have expired")
                    reader = table.scan(snapshot_id=snapshot).to_arrow_batch_reader()
                    connection.register("source", reader)
                    connection.execute("SET enable_external_access = false")
                else:
                    raise ValueError("Unknown dataset type")
                connection.execute("SET lock_configuration = true")
                yield connection
            finally:
                connection.close()
                if reader is not None:
                    reader.close()


def _columns(conn) -> list[dict]:
    return [
        {"name": row[0], "dtype": _dtype(str(row[1])), "sql_type": str(row[1])}
        for row in conn.execute("DESCRIBE source").fetchall()
    ]


def columns(store: Store, dataset: dict, search: str = "") -> dict:
    with source_connection(store, dataset) as conn:
        discovered = _columns(conn)
    query = search.casefold()
    return {
        "dataset_id": dataset["id"],
        "version": dataset["version"],
        "total": len(discovered),
        "columns": [
            {"name": c["name"], "dtype": c["dtype"]}
            for c in discovered
            if query in c["name"].casefold()
        ],
    }


def overview(store: Store, dataset: dict) -> dict:
    """Parquet는 Footer의 행 수를, CSV는 미계산 상태를 명시한다."""
    row_count = None
    basis = "not_computed"
    size = None
    if dataset["kind"] == "file":
        path = local_dataset_file(store, dataset)
        size = path.stat().st_size
        if path.suffix.lower() == ".parquet":
            row_count = pq.read_metadata(path).num_rows
            basis = "parquet_metadata"
    with source_connection(store, dataset) as conn:
        found = _columns(conn)
    return {
        "dataset_id": dataset["id"],
        "version": dataset["version"],
        "row_count": row_count,
        "row_count_basis": basis,
        "column_count": len(found),
        "size_bytes": size,
        "columns": [{"name": c["name"], "dtype": c["dtype"]} for c in found],
    }


def preview(store: Store, dataset: dict, limit: int = 30) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("Preview limit must be between 1 and 100")
    with source_connection(store, dataset) as conn:
        schema = {c["name"]: c["dtype"] for c in _columns(conn)}
        raw = _records(conn.execute("SELECT * FROM source LIMIT ?", [limit]))
        rows = [{key: _json_value(value) for key, value in row.items()} for row in raw]
    return {"schema": schema, "rows": rows, "returned_rows": len(rows)}


def profile(store: Store, dataset: dict, sample: int | None = 1000) -> dict:
    policy = DiscoveryPolicy.from_env()
    if sample is not None and not 1 <= sample <= policy.max_sample:
        raise ValueError("Invalid profile sample limit")
    with source_connection(store, dataset, policy) as conn:
        schema = _columns(conn)
        scope = "source" if sample is None else "(SELECT * FROM source LIMIT ?) AS sampled_source"
        params = [] if sample is None else [sample]
        parts = ["COUNT(*) AS __rows"]
        for number, c in enumerate(schema):
            name = quoted(c["name"])
            key = f"c{number}_"
            kind = c["sql_type"].upper()
            parts += [
                f"COUNT(*) - COUNT({name}) AS {key}null",
                f"COUNT(DISTINCT {name}) + CAST(COUNT({name}) < COUNT(*) AS INTEGER) AS {key}unique",
            ]
            if kind in ("FLOAT", "DOUBLE", "REAL"):
                parts.append(f"COUNT(*) FILTER (WHERE isnan({name})) AS {key}nan")
            if _numeric_type(kind):
                parts += [
                    f"MIN({name}) AS {key}min",
                    f"MAX({name}) AS {key}max",
                    f"AVG({name}) AS {key}mean",
                ]
        raw = _records(
            conn.execute(
                f"SELECT {', '.join(parts)} FROM {scope}",
                params,
            )
        )[0]
    result = []
    for number, c in enumerate(schema):
        key = f"c{number}_"
        kind = c["sql_type"].upper()
        entry = {
            "name": c["name"],
            "dtype": c["dtype"],
            "nulls": raw[key + "null"],
            "nan_count": raw.get(key + "nan"),
            "unique": raw[key + "unique"],
        }
        if key + "mean" in raw:
            entry.update(
                {
                    "min": _profile_number(raw[key + "min"]),
                    "max": _profile_number(raw[key + "max"]),
                    "mean": _profile_number(raw[key + "mean"]),
                }
            )
        result.append(entry)
    return {
        "sampled": sample is not None,
        "sample_limit": sample,
        "row_count": raw["__rows"],
        "columns": result,
    }


def explore(
    store: Store,
    dataset: dict,
    column: str,
    metric: str,
    group_by: str | None = None,
    *,
    scope: str = "sample",
) -> dict:
    """동일 집계 정의로 제한된 대화형 샘플과 전체 Worker Job을 실행한다."""
    if scope not in ("sample", "full"):
        raise ValueError("Invalid exploration scope")
    policy = DiscoveryPolicy.from_env()
    expressions = {
        "count": lambda col: "COUNT(*)",
        "null_count": lambda col: f"COUNT(*) - COUNT({col})",
        "mean": lambda col: f"AVG({col})",
        "min": lambda col: f"MIN({col})",
        "max": lambda col: f"MAX({col})",
    }
    if metric not in expressions:
        raise ValueError("Unsupported exploration metric")
    with source_connection(store, dataset, policy) as conn:
        actual = {c["name"]: c for c in _columns(conn)}
        if column not in actual or (group_by is not None and group_by not in actual):
            raise ValueError("Unknown exploration column")
        if metric in ("mean", "min", "max") and not _numeric_type(actual[column]["sql_type"]):
            raise ValueError("Numeric metric requires numeric column")
        value = expressions[metric](quoted(column))
        source = (
            f"(SELECT * FROM source LIMIT {policy.max_sample}) AS sample_source"
            if scope == "sample"
            else "source"
        )
        # SQL에서 비유한 집계값을 먼저 NULL로 치환해 올바른 그룹 순서와 LIMIT을 얻는다.
        # AVG는 DOUBLE이고 FLOAT/DOUBLE의 MIN/MAX도 비유한 값을 낼 수 있다.
        floating_result = metric == "mean" or (
            metric in ("min", "max")
            and actual[column]["sql_type"].upper() in ("FLOAT", "DOUBLE", "REAL")
        )
        normalized = (
            "CASE WHEN isfinite(raw_value) THEN raw_value ELSE NULL END"
            if floating_result
            else "raw_value"
        )
        if group_by:
            group = quoted(group_by)
            rows = _records(
                conn.execute(
                    "WITH aggregated AS ("
                    f"SELECT {group} AS category, {value} AS raw_value FROM {source} "
                    f"GROUP BY {group}) "
                    f"SELECT category, {normalized} AS value FROM aggregated "
                    f"ORDER BY value DESC NULLS LAST, category LIMIT {policy.max_groups + 1}"
                )
            )
            truncated = len(rows) > policy.max_groups
            rows = rows[: policy.max_groups]
        else:
            rows = _records(
                conn.execute(
                    f"WITH aggregated AS (SELECT {value} AS raw_value FROM {source}) "
                    f"SELECT NULL AS category, {normalized} AS value FROM aggregated"
                )
            )
            truncated = False
    # Worker JSON 저장 전에도 모든 값을 원시 JSON 타입으로 확정한다.
    for row in rows:
        row["category"] = _json_value(row["category"])
        row["value"] = _aggregate_number(row["value"])
    return {
        "dataset_id": dataset["id"],
        "version": dataset["version"],
        "scope": scope,
        "sample_limit": policy.max_sample if scope == "sample" else None,
        "metric": metric,
        "column": column,
        "group_by": group_by,
        "truncated": truncated,
        "rows": rows,
    }
