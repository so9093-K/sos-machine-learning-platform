"""파일 포맷의 공통 Reader 등록과 실행 엔진 간 계약을 검증한다."""

from pathlib import Path

import duckdb
import polars as pl
import pytest

from sos_platform.cache import _CACHE_FILE, _CACHE_PENDING
from sos_platform.delimited import parse_options
from sos_platform.errors import PlatformError
from sos_platform.file_formats import (
    SUPPORTED_EXTENSIONS,
    CsvReader,
    FileReader,
    _build_registry,
    reader_for_suffix,
    scan_file,
    upload_options_from_mime,
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def test_supported_extensions_share_registry_with_cache():
    """새 파일 포맷이 캐시의 허용 목록에서 누락되지 않는다."""
    assert SUPPORTED_EXTENSIONS
    for suffix in SUPPORTED_EXTENSIONS:
        assert reader_for_suffix(suffix.upper()) is reader_for_suffix(suffix)
        assert _CACHE_FILE.fullmatch("a" * 64 + suffix)
        assert _CACHE_PENDING.fullmatch("a" * 64 + suffix + ".pending-" + "b" * 32)
    assert not _CACHE_FILE.fullmatch("a" * 64 + ".csv.exe")
    assert not _CACHE_FILE.fullmatch("../" + "a" * 64 + ".csv")
    with pytest.raises(ValueError, match="Unsupported"):
        reader_for_suffix(".csv.exe")


def test_reader_registry_rejects_duplicate_or_noncanonical_extensions():
    """새 형식을 등록할 때 확장자 충돌을 숨기지 않는다."""

    class DuplicateReader(FileReader):
        extensions = (".csv",)

    class UppercaseReader(FileReader):
        extensions = (".NEW",)

    with pytest.raises(ValueError, match="extension"):
        _build_registry((CsvReader(), DuplicateReader()))
    with pytest.raises(ValueError, match="extension"):
        _build_registry((UppercaseReader(),))


@pytest.mark.parametrize("suffix", [".txt", ".tsv"])
@pytest.mark.parametrize("has_header", [False, True])
def test_tabular_reader_keeps_same_columns_and_rows_across_engines(
    tmp_path: Path, suffix: str, has_header: bool
):
    """같은 해석 설정은 행·컬럼을 두 실행 엔진에서 동일하게 보존한다."""
    payload = 'score\treview\n5\t배공빠르고 굿\n2\t문장에 "따옴표" 포함\n'
    path = tmp_path / ("sample" + suffix)
    path.write_bytes(payload.encode("utf-8"))
    reader = reader_for_suffix(suffix)
    options = reader.upload_options({"has_header": has_header})
    reader.validate(path, options)
    rows = reader.scan_polars(path, options).collect().to_dicts()

    with duckdb.connect(":memory:") as conn:
        reader.create_duckdb_view(conn, path, options, _literal, _quoted)
        db_columns = [row[0] for row in conn.execute("DESCRIBE source").fetchall()]
        db_rows = conn.execute("SELECT * FROM source ORDER BY 1").fetchall()

    expected_names = ["score", "review"] if has_header else ["unnamed_1", "unnamed_2"]
    assert list(rows[0]) == expected_names
    assert db_columns == expected_names
    assert len(rows) == len(db_rows) == (2 if has_header else 3)
    assert "따옴표" in str(rows[-1]) + str(db_rows)


def test_legacy_csv_defaults_and_parquet_metadata_are_preserved(tmp_path: Path):
    csv_file = tmp_path / "old.csv"
    csv_file.write_text('label,value\n"a,b",5\n"c,d",2\n', encoding="utf-8")
    csv_reader = reader_for_suffix(".csv")
    assert csv_reader.upload_options(None) is None
    assert scan_file(csv_file, {"parse_options": None}).collect().height == 2
    with duckdb.connect(":memory:") as conn:
        csv_reader.create_duckdb_view(conn, csv_file, None, _literal, _quoted)
        assert conn.execute("SELECT count(*) FROM source").fetchone()[0] == 2

    parquet_file = tmp_path / "old.parquet"
    pl.DataFrame({"value": [1, 2, 3]}).write_parquet(parquet_file)
    parquet_reader = reader_for_suffix(".parquet")
    assert parquet_reader.upload_options(None) is None
    assert parquet_reader.metadata_row_count(parquet_file) == 3
    assert parquet_reader.scan_polars(parquet_file, None).collect().height == 3
    with duckdb.connect(":memory:") as conn:
        parquet_reader.create_duckdb_view(conn, parquet_file, None, _literal, _quoted)
        assert conn.execute("SELECT count(*) FROM source").fetchone()[0] == 3


def test_tabular_upload_requires_header_and_rejects_wrong_mime():
    assert upload_options_from_mime("example.txt", "text/tab-separated-values; header=absent") == {
        "has_header": False
    }
    assert upload_options_from_mime("example.tsv", "text/tab-separated-values; header=present") == {
        "has_header": True
    }
    assert upload_options_from_mime("example.csv", "text/csv") is None
    for name, content_type in [
        ("example.txt", "text/plain"),
        ("example.tsv", "text/tab-separated-values"),
        ("example.tsv", "text/tab-separated-values; header=maybe"),
        ("example.unknown", "application/octet-stream"),
    ]:
        with pytest.raises(PlatformError):
            upload_options_from_mime(name, content_type)
    with pytest.raises(PlatformError):
        reader_for_suffix(".txt").upload_options(None)
    with pytest.raises(PlatformError):
        reader_for_suffix(".csv").upload_options({"has_header": False})


def test_invalid_stored_reader_options_are_not_silently_ignored(tmp_path: Path):
    file = tmp_path / "sample.tsv"
    file.write_text("5\tgood\n", encoding="utf-8")
    reader = reader_for_suffix(".tsv")
    for options in [
        None,
        {},
        {"format": "tsv", "has_header": "false"},
        {"format": "tsv", "has_header": False, "unrecognized": True},
    ]:
        with pytest.raises(ValueError, match="read settings"):
            reader.scan_polars(file, options)
    assert parse_options({"parse_options": '{"format":"tsv","has_header":false}'}) == {
        "format": "tsv",
        "has_header": False,
    }


def test_tabular_reader_preserves_late_mixed_type_values(tmp_path: Path):
    """초기 추론 범위 밖의 문자열도 원본 행의 유효한 값으로 읽혀야 한다."""
    path = tmp_path / "late-mixed.tsv"
    content = "".join(f"{i}\titem-{i}\n" for i in range(150)) + "unknown\tlast-row\n"
    path.write_text(content, encoding="utf-8")
    reader = reader_for_suffix(".tsv")
    options = reader.upload_options({"has_header": False})
    reader.validate(path, options)

    polars_rows = reader.scan_polars(path, options).collect().to_dicts()
    with duckdb.connect(":memory:") as conn:
        reader.create_duckdb_view(conn, path, options, _literal, _quoted)
        duckdb_rows = conn.execute("SELECT * FROM source").fetchall()

    assert len(polars_rows) == len(duckdb_rows) == 151
    assert polars_rows[-1]["unnamed_1"] == "unknown"
    assert duckdb_rows[-1][0] == "unknown"


def test_long_tabular_text_field_without_implicit_csv_limit(tmp_path: Path):
    """파일 정책 밖의 숨은 CSV 필드 길이 한도 때문에 유효한 TSV를 거부하지 않는다."""
    path = tmp_path / "long-text.tsv"
    path.write_text("code\treview\r\n1\t" + "가" * 150_000 + "\r\n", encoding="utf-8")
    reader = reader_for_suffix(".tsv")
    options = reader.upload_options({"has_header": True})
    reader.validate(path, options)
    assert reader.scan_polars(path, options).collect().height == 1
