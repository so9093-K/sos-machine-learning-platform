"""CSV·TSV 헤더 이름을 DuckDB(조회·입력 검사)와 Polars(변환·학습·Scoring)가 같게 읽는지 확인한다.

컬럼 조회에서 보인 이름이 실행에서 "컬럼 없음"이 되면 사용자는 원인을 알 수 없다.
"""

import duckdb
import pytest

from sos_platform.file_formats import reader_for_suffix


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _duckdb_names(path, options):
    with duckdb.connect() as conn:
        reader_for_suffix(path.suffix).create_duckdb_view(conn, path, options, _literal, _quoted)
        return [row[0] for row in conn.execute("DESCRIBE source").fetchall()]


@pytest.mark.parametrize(("suffix", "options", "text"), [
    # 앞뒤 공백, 인용 안의 공백, 공백을 지우면 겹치는 이름.
    (".csv", None, ' Flow Duration ,b,"  c  ",d,  d\n1,2,3,4,5\n'),
    (".tsv", {"format": "tsv", "has_header": True}, " Flow Duration \tb\td\t  d\n1\t2\t4\t5\n"),
])
def test_execution_uses_the_column_names_shown_by_discovery(tmp_path, suffix, options, text):
    path = tmp_path / f"source{suffix}"
    path.write_text(text, encoding="utf-8")
    shown = _duckdb_names(path, options)
    frame = reader_for_suffix(suffix).scan_polars(path, options).collect()
    assert shown[0] == "Flow Duration"
    assert frame.columns == shown
    # 이름만 바뀌고 값의 위치는 그대로다.
    assert frame.row(0)[0] == 1 and frame.row(0)[-1] == 5


def test_headers_without_surrounding_spaces_are_unchanged(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    assert reader_for_suffix(".csv").scan_polars(path, None).collect().columns == ["a", "b"]
