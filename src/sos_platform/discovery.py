"""불변 Dataset을 대상으로 DuckDB 탐색·통계 연산을 수행한다.

외부 SQL과 경로를 받지 않는다. 요청 컬럼은 실제 스키마에서 확인하며
Dataset 판본과 접근 권한의 판단은 호출자인 API·Worker가 담당한다.
"""

import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import duckdb
import pyarrow.parquet as pq

from .datasets import iceberg_catalog, local_dataset_file
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


def _dtype(dtype: str) -> str:
    """기존 Profile·Feature UI의 Polars 타입 이름을 보존한다."""
    kind = dtype.upper()
    if kind in (
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
    ):
        return "Int64"
    if kind in ("FLOAT", "DOUBLE", "REAL"):
        return "Float64"
    if kind.startswith("DECIMAL"):
        return kind.title()
    if kind in ("VARCHAR", "CHAR", "TEXT"):
        return "String"
    if kind in ("BOOLEAN", "BOOL"):
        return "Boolean"
    if kind == "DATE":
        return "Date"
    if kind.startswith("TIMESTAMP"):
        return "Datetime(time_unit='us', time_zone=None)"
    return dtype


def _number(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _records(cursor) -> list[dict]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


@contextmanager
def source_connection(
    store: Store, dataset: dict, policy: DiscoveryPolicy | None = None
) -> Iterator[duckdb.DuckDBPyConnection]:
    """작업마다 새로운 DuckDB 연결을 만들고 허용한 원본만 스캔한다.

    Iceberg는 PyIceberg가 지정 Snapshot과 삭제 파일을 해석한 Arrow 배치로 연결한다.
    """
    policy = policy or DiscoveryPolicy.from_env()
    scratch = store.home / "cache" / "discovery-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
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
        rows = _records(conn.execute("SELECT * FROM source LIMIT ?", [limit]))
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
            if kind in (
                "FLOAT",
                "DOUBLE",
                "REAL",
                "TINYINT",
                "SMALLINT",
                "INTEGER",
                "BIGINT",
                "UTINYINT",
                "USMALLINT",
                "UINTEGER",
                "UBIGINT",
            ) or kind.startswith("DECIMAL"):
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
                    "min": _number(raw[key + "min"]),
                    "max": _number(raw[key + "max"]),
                    "mean": _number(raw[key + "mean"]),
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
        if metric in ("mean", "min", "max") and not (
            actual[column]["dtype"].startswith(("Int", "Float", "Decimal"))
        ):
            raise ValueError("Numeric metric requires numeric column")
        value = expressions[metric](quoted(column))
        source = (
            f"(SELECT * FROM source LIMIT {policy.max_sample}) AS sample_source"
            if scope == "sample"
            else "source"
        )
        if group_by:
            group = quoted(group_by)
            rows = _records(
                conn.execute(
                    f"SELECT {group} AS category, {value} AS value FROM {source} "
                    f"GROUP BY {group} ORDER BY value DESC NULLS LAST, category "
                    f"LIMIT {policy.max_groups + 1}"
                )
            )
            truncated = len(rows) > policy.max_groups
            rows = rows[: policy.max_groups]
        else:
            rows = _records(conn.execute(f"SELECT NULL AS category, {value} AS value FROM {source}"))
            truncated = False
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
