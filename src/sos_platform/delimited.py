"""CSV·TSV/TXT 텍스트 테이블을 두 실행 엔진이 같은 의미로 읽도록 등록 시 전수 확인한다."""

import csv
import json
import sys
from pathlib import Path

from .errors import PlatformError

TABULAR_SUFFIXES = (".tsv", ".txt")
# 쉼표 계약으로 읽으면 한 컬럼으로 합쳐지는 다른 구분자. 조용히 한 컬럼 Dataset으로 등록하지 않는다.
_FOREIGN_DELIMITERS = (";", "\t", "|")


def parse_options(dataset: dict) -> dict | None:
    """Dataset에 귀속된 입력 해석 설정. 기존 파일의 NULL은 별도 설정 없음."""
    value = dataset.get("parse_options")
    return json.loads(value) if isinstance(value, str) else value


def _validate_header(names: list[str]) -> None:
    """빈 이름과 대소문자만 다른 중복 이름을 거절한다.

    DuckDB는 대소문자만 다른 이름을 자동으로 바꾸고 Polars는 그대로 두므로,
    허용하면 Data Explorer와 ML Job이 서로 다른 컬럼 이름을 보게 된다.
    """
    if any(not name.strip() for name in names):
        raise PlatformError("SOS-UPLOAD-STRUCTURE")
    if len({name.casefold() for name in names}) != len(names):
        raise PlatformError("SOS-UPLOAD-STRUCTURE")


def _tab_fields(line: str) -> list[str]:
    """따옴표를 일반 문자로 취급하며 물리적 한 행의 탭 필드만 분리한다."""
    if line.endswith("\r\n"):
        line = line[:-2]
    elif line.endswith(("\n", "\r")):
        line = line[:-1]
    return line.split("\t")


def validate_tabular_text(path: Path, *, has_header: bool) -> int:
    """UTF-8 탭 테이블의 열 폭을 전수 확인하고 데이터 행 수를 반환한다.

    CSV 파서의 숨은 필드 길이 제한은 사용하지 않는다.
    """
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            first = next(stream, None)
            if first is None:
                raise PlatformError("SOS-UPLOAD-INVALID")
            header = _tab_fields(first)
            if len(header) < 2:
                raise PlatformError("SOS-UPLOAD-INVALID")
            if has_header:
                _validate_header(header)
            rows = 0 if has_header else 1
            for line in stream:
                if len(_tab_fields(line)) != len(header):
                    raise PlatformError("SOS-UPLOAD-STRUCTURE")
                rows += 1
            return rows
    except UnicodeError as exc:
        raise PlatformError("SOS-UPLOAD-ENCODING") from exc
    except OSError as exc:
        raise PlatformError("SOS-UPLOAD-INVALID") from exc


def validate_csv_text(path: Path) -> int:
    """UTF-8·쉼표 구분·첫 행 헤더·동일 열 폭의 CSV 계약을 전수 확인하고 데이터 행 수를 반환한다.

    빈 행은 Polars가 NULL 행으로, DuckDB가 건너뛰는 행으로 해석하므로 거절한다.
    """
    # 업로드 한도 안의 긴 필드를 csv 모듈의 숨은 기본 필드 한도로 거절하지 않는다.
    csv.field_size_limit(sys.maxsize)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            records = csv.reader(stream, strict=True)
            header = next(records, None)
            if header is None:
                raise PlatformError("SOS-UPLOAD-INVALID")
            _validate_header(header)
            if len(header) == 1 and any(mark in header[0] for mark in _FOREIGN_DELIMITERS):
                raise PlatformError("SOS-UPLOAD-STRUCTURE")
            rows = 0
            for record in records:
                if len(record) != len(header):
                    raise PlatformError("SOS-UPLOAD-STRUCTURE")
                rows += 1
            return rows
    except UnicodeError as exc:
        raise PlatformError("SOS-UPLOAD-ENCODING") from exc
    except csv.Error as exc:
        raise PlatformError("SOS-UPLOAD-STRUCTURE") from exc
    except OSError as exc:
        raise PlatformError("SOS-UPLOAD-INVALID") from exc
