"""헤더 유무가 확정된 TSV/TXT를 원본 변경 없이 일관되게 읽는다."""

import json
from pathlib import Path

from .errors import PlatformError

TABULAR_SUFFIXES = (".tsv", ".txt")


def parse_options(dataset: dict) -> dict | None:
    """Dataset에 귀속된 입력 해석 설정. 기존 파일의 NULL은 별도 설정 없음."""
    value = dataset.get("parse_options")
    return json.loads(value) if isinstance(value, str) else value


def _tab_fields(line: str) -> list[str]:
    """따옴표를 일반 문자로 취급하며 물리적 한 행의 탭 필드만 분리한다."""
    if line.endswith("\r\n"):
        line = line[:-2]
    elif line.endswith(("\n", "\r")):
        line = line[:-1]
    return line.split("\t")


def validate_tabular_text(path: Path, *, has_header: bool) -> None:
    """UTF-8 탭 테이블의 열 폭을 전수 확인한다. CSV 파서의 숨은 필드 길이 제한은 사용하지 않는다."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            first = next(stream, None)
            if first is None:
                raise PlatformError("SOS-UPLOAD-INVALID")
            header = _tab_fields(first)
            if len(header) < 2:
                raise PlatformError("SOS-UPLOAD-INVALID")
            if has_header and (
                any(not name.strip() for name in header) or len(set(header)) != len(header)
            ):
                raise PlatformError("SOS-UPLOAD-INVALID")
            for line in stream:
                if len(_tab_fields(line)) != len(header):
                    raise PlatformError("SOS-UPLOAD-INVALID")
    except (UnicodeError, OSError) as exc:
        raise PlatformError("SOS-UPLOAD-INVALID") from exc
