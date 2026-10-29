"""Scoring Attempt의 스테이징 소유권·용량·회수. 추론 Runtime을 참조하지 않는다."""

import shutil
from pathlib import Path


def scoring_stage_prefix(scoring_output: Path) -> str:
    """Worker Attempt 결과 경로에 귀속된 임시 Scoring Stage 디렉터리 접두어."""
    return f"ml-score-{scoring_output.name}-"


def scoring_stage_directories(home: Path, scoring_output: Path) -> list[Path]:
    """해당 Attempt가 소유하는 Stage만 탐색하고 심볼릭 링크는 거절한다."""
    root = home / "results"
    if not root.is_dir():
        return []
    prefix = scoring_stage_prefix(scoring_output)
    matched = []
    for item in root.iterdir():
        if not item.name.startswith(prefix):
            continue
        if item.is_symlink():
            raise ValueError("Scoring Stage must be an owned directory")
        # 자식이 성공적으로 TemporaryDirectory를 정리한 직후일 수 있다.
        if not item.exists():
            continue
        if not item.is_dir():
            raise ValueError("Scoring Stage must be an owned directory")
        matched.append(item)
    return matched


def scoring_stage_bytes(home: Path, scoring_output: Path) -> int:
    """임시 입력 Stage와 현재 출력 Parquet의 실제 디스크 사용량."""
    try:
        size = scoring_output.stat().st_size if scoring_output.is_file() else 0
    except FileNotFoundError:
        size = 0
    for root in scoring_stage_directories(home, scoring_output):
        try:
            for entry in root.rglob("*"):
                if entry.is_symlink():
                    raise ValueError("Scoring Stage cannot contain symbolic links")
                try:
                    if entry.is_file():
                        size += entry.stat().st_size
                except FileNotFoundError:
                    # 임시 파일이 폴링 사이에 정상 삭제될 수 있다.
                    continue
        except FileNotFoundError:
            continue
    return size


def cleanup_scoring_stage(home: Path, scoring_output: Path) -> None:
    """중단·재시도 Attempt에 한해 남은 임시 Stage 디렉터리를 제거한다."""
    for directory in scoring_stage_directories(home, scoring_output):
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            continue
