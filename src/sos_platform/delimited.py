"""헤더 유무가 확정된 TSV/TXT를 원본 변경 없이 일관되게 읽는다."""

import csv
import json
from pathlib import Path

import polars as pl

from .errors import PlatformError

TABULAR_SUFFIXES = (".tsv", ".txt")


def parse_options(dataset: dict) -> dict | None:
    """Dataset에 귀속된 입력 해석 설정. 기존 파일의 NULL은 별도 설정 없음."""
    value = dataset.get("parse_options")
    return json.loads(value) if isinstance(value, str) else value


def validate_tabular_text(path: Path, *, has_header: bool) -> None:
    """UTF-8 탭 테이블의 행 폭과 헤더를 전체 입력에서 확인한다."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t", strict=True)
            header = next(reader, None)
            if header is None or len(header) < 2:
                raise PlatformError("SOS-UPLOAD-INVALID")
            if has_header and (any(not name.strip() for name in header) or
                               len(set(header)) != len(header)):
                raise PlatformError("SOS-UPLOAD-INVALID")
            for row in reader:
                if len(row) != len(header):
                    raise PlatformError("SOS-UPLOAD-INVALID")
    except (UnicodeError, csv.Error, OSError) as exc:
        raise PlatformError("SOS-UPLOAD-INVALID") from exc


def scan_file(path: Path, dataset: dict) -> pl.LazyFrame:
    """Polars Worker·Preview가 저장된 Dataset 해석 설정을 공유한다."""
    ext = path.suffix.lower()
    if ext == ".parquet":
        return pl.scan_parquet(path)
    if ext == ".csv":
        return pl.scan_csv(path, try_parse_dates=True)
    if ext not in TABULAR_SUFFIXES:
        raise ValueError("Unsupported file Dataset extension")
    opts = parse_options(dataset)
    if not isinstance(opts, dict) or opts.get("format") != "tsv" or not isinstance(opts.get("has_header"), bool):
        raise ValueError("Missing tabular Dataset parse settings")
    frame = pl.scan_csv(path, separator="\t", has_header=opts["has_header"], try_parse_dates=True)
    if not opts["has_header"]:
        names = frame.collect_schema().names()
        frame = frame.rename({name: f"unnamed_{index}" for index, name in enumerate(names, 1)})
    return frame
