"""테이블형 파일 포맷의 등록·읽기 계약과 실행 엔진별 어댑터."""

from collections.abc import Callable
from email.message import Message
from pathlib import Path

import polars as pl

from .delimited import TABULAR_SUFFIXES, parse_options, validate_tabular_text
from .errors import PlatformError

SqlQuoter = Callable[[str], str]


class FileReader:
    """한 파일 포맷의 옵션·검증·두 실행 엔진 진입점을 소유한다."""

    extensions: tuple[str, ...] = ()

    def header_from_mime(self, content_type: str | None) -> bool | None:
        """기존 업로드 API의 파일 파트에서 필요한 입력 옵션을 읽는다."""
        return None

    def upload_options(self, has_header: bool | None) -> dict | None:
        """업로드 옵션을 검증해 불변 Dataset에 저장할 값을 반환한다."""
        if has_header is not None:
            raise PlatformError("SOS-UPLOAD-INVALID")
        return None

    def validate(self, path: Path, options: dict | None) -> None:
        """포맷별 입력 검증을 수행한다. 기존 CSV·Parquet 계약은 유지한다."""
        if options is not None:
            raise PlatformError("SOS-UPLOAD-INVALID")

    def scan_polars(self, path: Path, options: dict | None) -> pl.LazyFrame:
        raise NotImplementedError

    def create_duckdb_view(
        self, conn, path: Path, options: dict | None, literal: SqlQuoter, quoted: SqlQuoter
    ) -> None:
        raise NotImplementedError

    def metadata_row_count(self, path: Path) -> int | None:
        """스캔 없이 행 수를 알 수 없는 포맷은 계산하지 않는다."""
        return None


class CsvReader(FileReader):
    """기존 CSV 파서의 기본값을 변경하지 않는 호환 리더."""

    extensions = (".csv",)

    def scan_polars(self, path: Path, options: dict | None) -> pl.LazyFrame:
        self.validate(path, options)
        return pl.scan_csv(path, try_parse_dates=True)

    def create_duckdb_view(
        self, conn, path: Path, options: dict | None, literal: SqlQuoter, quoted: SqlQuoter
    ) -> None:
        self.validate(path, options)
        conn.execute(f"CREATE TEMP VIEW source AS SELECT * FROM read_csv({literal(str(path))})")


class ParquetReader(FileReader):
    """Parquet가 보유한 스키마·Footer 정보를 직접 사용한다."""

    extensions = (".parquet",)

    def scan_polars(self, path: Path, options: dict | None) -> pl.LazyFrame:
        self.validate(path, options)
        return pl.scan_parquet(path)

    def create_duckdb_view(
        self, conn, path: Path, options: dict | None, literal: SqlQuoter, quoted: SqlQuoter
    ) -> None:
        self.validate(path, options)
        conn.execute(f"CREATE TEMP VIEW source AS SELECT * FROM read_parquet({literal(str(path))})")

    def metadata_row_count(self, path: Path) -> int:
        import pyarrow.parquet as pq

        return pq.read_metadata(path).num_rows


class TabDelimitedReader(FileReader):
    """헤더를 명시적으로 확인한 탭 구분 파일만 지원한다."""

    extensions = TABULAR_SUFFIXES

    def header_from_mime(self, content_type: str | None) -> bool:
        mime = Message()
        mime["Content-Type"] = content_type or ""
        if mime.get_content_type() != "text/tab-separated-values":
            raise PlatformError("SOS-UPLOAD-INVALID")
        declaration = mime.get_param("header")
        if declaration not in ("present", "absent"):
            raise PlatformError("SOS-UPLOAD-INVALID")
        return declaration == "present"

    def upload_options(self, has_header: bool | None) -> dict:
        if not isinstance(has_header, bool):
            raise PlatformError("SOS-UPLOAD-INVALID")
        return {"format": "tsv", "has_header": has_header}

    @staticmethod
    def _header(options: dict | None) -> bool:
        if (
            not isinstance(options, dict)
            or set(options) != {"format", "has_header"}
            or options["format"] != "tsv"
            or not isinstance(options["has_header"], bool)
        ):
            raise ValueError("Missing or invalid tabular Dataset read settings")
        return options["has_header"]

    def validate(self, path: Path, options: dict | None) -> None:
        validate_tabular_text(path, has_header=self._header(options))

    def scan_polars(self, path: Path, options: dict | None) -> pl.LazyFrame:
        frame = pl.scan_csv(
            path,
            separator="\t",
            quote_char=None,
            has_header=self._header(options),
            try_parse_dates=True,
        )
        if not self._header(options):
            names = frame.collect_schema().names()
            frame = frame.rename(
                {name: f"unnamed_{index}" for index, name in enumerate(names, 1)}
            )
        return frame

    def create_duckdb_view(
        self, conn, path: Path, options: dict | None, literal: SqlQuoter, quoted: SqlQuoter
    ) -> None:
        header = self._header(options)
        source = (
            f"read_csv({literal(str(path))}, delim={literal(chr(9))}, quote='', "
            f"header={str(header).lower()})"
        )
        if header:
            conn.execute(f"CREATE TEMP VIEW source AS SELECT * FROM {source}")
            return
        names = [
            row[0]
            for row in conn.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        ]
        projection = ", ".join(
            f"{quoted(name)} AS {quoted('unnamed_' + str(i))}"
            for i, name in enumerate(names, 1)
        )
        conn.execute(f"CREATE TEMP VIEW source AS SELECT {projection} FROM {source}")


# 고정된 내부 레지스트리. 임의의 사용자 파일을 실행 코드로 로드하지 않는다.
_READERS: tuple[FileReader, ...] = (CsvReader(), ParquetReader(), TabDelimitedReader())
_BY_SUFFIX = {
    suffix: reader for reader in _READERS for suffix in reader.extensions
}
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(_BY_SUFFIX)


def reader_for_suffix(suffix: str) -> FileReader:
    """지원하는 확장자만 허용하며 알 수 없는 파일을 추측해 파싱하지 않는다."""
    try:
        return _BY_SUFFIX[suffix.lower()]
    except KeyError as exc:
        raise ValueError("Unsupported file Dataset extension") from exc


def upload_header_from_mime(name: str, content_type: str | None) -> bool | None:
    """호환 업로드 API에서 형식별 MIME 해석을 어댑터에 위임한다."""
    try:
        reader = reader_for_suffix(Path(name).suffix)
    except ValueError as exc:
        raise PlatformError("SOS-UPLOAD-FORMAT") from exc
    return reader.header_from_mime(content_type)


def dataset_reader(dataset: dict, path: Path) -> tuple[FileReader, dict | None]:
    """로컬 캐시 파일과 영속 Dataset 옵션을 함께 해석한다."""
    return reader_for_suffix(path.suffix), parse_options(dataset)


def scan_file(path: Path, dataset: dict) -> pl.LazyFrame:
    reader, options = dataset_reader(dataset, path)
    return reader.scan_polars(path, options)


def create_duckdb_file_view(
    conn, path: Path, dataset: dict, literal: SqlQuoter, quoted: SqlQuoter
) -> None:
    reader, options = dataset_reader(dataset, path)
    reader.create_duckdb_view(conn, path, options, literal, quoted)
