"""Scoring 결과의 점수순 관측값 색인. 결과마다 한 번 만들어 불변 조각으로 게시하고 조회가 재사용한다.

- order 조각: 점수 내림차순·결과 행 번호 오름차순으로 정렬한 관측값(점수·Feature·관측 시각·관측치 ID).
- position 조각: 결과 행 번호 순서의 점수 순위. 개별 관측값을 행 번호로 찾을 때 쓴다.

조각마다 행 수가 일정해 순위나 행 번호로 필요한 조각만 내려받는다(해시 검증·로컬 캐시). 페이지를 넘길 때
결과 전체를 다시 정렬하지 않으며, 결과 파일 전체를 로컬에 둘 필요도 없다. 결과 크기와 한 번의 응답
한도(페이지 크기)는 별개다.
"""

from bisect import bisect_right
from collections.abc import Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .cache import cached_dataset_file
from .duckdb_spill import SpillBudget, literal, quoted, spill_connection

ORDER_PART_ROWS = 65_536
POSITION_PART_ROWS = 1_048_576
_ROW_GROUP_ROWS = 4_096
_OBSERVATION_COLUMNS = ("row_index", "anomaly_score", "features", "observed_at", "observation_id")


class _PartWriter:
    """정해진 행 수마다 새 조각 파일을 여는 Writer."""

    def __init__(self, directory: Path, kind: str, rows: int):
        self.directory, self.kind, self.rows = directory, kind, rows
        self.part, self.filled, self.writer = 0, 0, None

    def write(self, table: pa.Table) -> None:
        while table.num_rows:
            if self.writer is None:
                self.writer = pq.ParquetWriter(
                    self.directory / f"{self.kind}-{self.part:06d}.parquet", table.schema,
                )
            take = min(self.rows - self.filled, table.num_rows)
            self.writer.write_table(table.slice(0, take), row_group_size=_ROW_GROUP_ROWS)
            table, self.filled = table.slice(take), self.filled + take
            if self.filled == self.rows:
                self.close()

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer, self.part, self.filled = None, self.part + 1, 0


def build_observation_index(scores: Path, out: Path, budget: SpillBudget) -> None:
    """Scoring 결과 파일에서 order·position 조각을 `out`에 만든다.

    정렬은 DuckDB가 Stage에 Spill하며 수행하고 결과는 배치로 순회한다. 행 번호와 순위의 짝은 Stage에
    기록한 뒤 행 번호 순으로 외부 정렬해 position 조각을 만든다.

    Raises:
        ValueError: 결과의 행 번호가 0부터 이어지는 순서가 아니다.
        PlatformError: Stage 공간이나 메모리로 정렬을 수용할 수 없다.
    """
    with pq.ParquetFile(scores) as reader:
        columns = [name for name in _OBSERVATION_COLUMNS if name in reader.schema_arrow.names]
        total = reader.metadata.num_rows
    readable = [] if scores.resolve().is_relative_to(budget.stage.resolve()) else [scores]
    out.mkdir()
    pairs = budget.stage / "rank-pairs.parquet"
    order = _PartWriter(out, "order", ORDER_PART_ROWS)
    pair_schema = pa.schema([("row_index", pa.int64()), ("rank", pa.int64())])
    rank = 0
    try:
        with spill_connection(budget, readable=readable) as conn, \
                pq.ParquetWriter(pairs, pair_schema) as pair_writer:
            reader = conn.execute(
                f"SELECT {', '.join(quoted(c) for c in columns)} "
                f"FROM read_parquet({literal(str(scores))}) "
                f"ORDER BY {quoted('anomaly_score')} DESC, {quoted('row_index')} ASC"
            ).to_arrow_reader(batch_size=ORDER_PART_ROWS)
            try:
                for batch in reader:
                    table = pa.Table.from_batches([batch])
                    order.write(table)
                    pair_writer.write_table(pa.Table.from_arrays(
                        [table.column("row_index").cast(pa.int64()),
                         pa.array(range(rank, rank + table.num_rows), type=pa.int64())],
                        schema=pair_schema,
                    ))
                    rank += table.num_rows
            finally:
                reader.close()
        order.close()
        position = _PartWriter(out, "position", POSITION_PART_ROWS)
        expected = 0
        with spill_connection(budget) as conn:
            reader = conn.execute(
                f"SELECT {quoted('row_index')}, {quoted('rank')} FROM read_parquet({literal(str(pairs))}) "
                f"ORDER BY {quoted('row_index')}"
            ).to_arrow_reader(batch_size=ORDER_PART_ROWS)
            try:
                for batch in reader:
                    indices = batch.column(0).to_numpy(zero_copy_only=False)
                    if indices.size and (
                        indices[0] != expected or (indices[1:] != indices[:-1] + 1).any()
                    ):
                        raise ValueError("Scoring result row order does not match its row indices")
                    expected += indices.size
                    position.write(pa.Table.from_arrays([batch.column(1)], names=["rank"]))
            finally:
                reader.close()
        position.close()
    finally:
        pairs.unlink(missing_ok=True)
    if rank != total or expected != total:
        raise ValueError("Observation index row count differs from the Scoring result")


def part_files(directory: Path) -> list[dict]:
    """조각 디렉터리의 조각 목록. 순위(또는 행 번호)의 시작과 행 수를 파일 순서로 정한다."""
    parts = []
    for kind in ("order", "position"):
        first = 0
        for number, path in enumerate(sorted(directory.glob(f"{kind}-*.parquet"))):
            if path.name != f"{kind}-{number:06d}.parquet":
                raise ValueError("Observation index parts are not contiguous")
            with pq.ParquetFile(path) as reader:
                rows = reader.metadata.num_rows
            parts.append({"kind": kind, "part": number, "first_position": first,
                          "row_count": rows, "path": path})
            first += rows
    return parts


def _part_containing(parts: Sequence[dict], position: int) -> dict | None:
    index = bisect_right([part["first_position"] for part in parts], position) - 1
    if index < 0:
        return None
    part = parts[index]
    return part if position < part["first_position"] + part["row_count"] else None


def _read_rows(store, part: dict, start: int, count: int, columns: list[str] | None) -> pa.Table:
    """조각을 검증·캐시해 조각 안 [start, start+count) 행만 해당 Row Group에서 읽는다."""
    with cached_dataset_file(store, {
        "kind": "file", "version": part["sha256"], "location": part["location"],
    }) as path, pq.ParquetFile(path) as reader:
        groups, offset, skip = [], 0, None
        for group in range(reader.num_row_groups):
            length = reader.metadata.row_group(group).num_rows
            if offset + length > start and offset < start + count:
                if skip is None:
                    skip = start - offset
                groups.append(group)
            offset += length
        table = reader.read_row_groups(groups, columns=columns)
    return table.slice(skip or 0, count)


def ranked_rows(store, parts: Sequence[dict], start: int, count: int) -> list[dict]:
    """점수 순위 [start, start+count)의 관측값."""
    order = [part for part in parts if part["kind"] == "order"]
    rows: list[dict] = []
    position = start
    while len(rows) < count:
        part = _part_containing(order, position)
        if part is None:
            break
        offset = position - part["first_position"]
        take = min(count - len(rows), part["row_count"] - offset)
        rows.extend(_read_rows(store, part, offset, take, None).to_pylist())
        position += take
    return rows


def rank_of_row(store, parts: Sequence[dict], row_index: int) -> int | None:
    """결과 행 번호의 점수 순위. 결과에 없는 행 번호면 None이다."""
    part = _part_containing([part for part in parts if part["kind"] == "position"], row_index)
    if part is None:
        return None
    value = _read_rows(store, part, row_index - part["first_position"], 1, ["rank"])
    return int(value.column(0)[0].as_py())
