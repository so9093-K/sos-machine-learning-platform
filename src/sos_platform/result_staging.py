"""결과를 만드는 Attempt의 임시 Stage·결과 파일 소유권·용량·회수.

Scoring·판정 정책·평가·설명·Feature 조회·관측값 색인은 Attempt 소유 경로(owner) 옆에 임시 Stage
디렉터리(`stage-{owner}-*`)와 게시 전 결과 조각 디렉터리(`{owner}.parts`)를 둔다. Worker는 같은 측정으로
크기를 감시하고 종료·복구 때 회수한다. 계산 Runtime을 참조하지 않는다.
"""

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


def result_stage_prefix(owner: Path) -> str:
    """Attempt 소유 경로에 귀속된 임시 Stage 디렉터리 접두어."""
    return f"stage-{owner.name}-"


def result_parts_dir(owner: Path) -> Path:
    """게시 전 결과 조각(예: 관측값 색인)을 두는 Attempt 소유 디렉터리."""
    return owner.with_suffix(".parts")


@contextmanager
def result_stage(owner: Path) -> Iterator[Path]:
    """이 Attempt가 소유하고 Worker가 감시·회수하는 임시 디렉터리."""
    with TemporaryDirectory(prefix=result_stage_prefix(owner), dir=owner.parent) as directory:
        yield Path(directory)


def _owned_directories(owner: Path) -> list[Path]:
    """해당 Attempt가 소유하는 Stage·조각 디렉터리만 찾고 심볼릭 링크는 거절한다."""
    root = owner.parent
    if not root.is_dir():
        return []
    prefix = result_stage_prefix(owner)
    parts = result_parts_dir(owner)
    matched = []
    for item in root.iterdir():
        if not (item.name.startswith(prefix) or item == parts):
            continue
        if item.is_symlink():
            raise ValueError("Result Stage must be an owned directory")
        # 자식이 TemporaryDirectory를 정리한 직후일 수 있다.
        if not item.exists():
            continue
        if not item.is_dir():
            raise ValueError("Result Stage must be an owned directory")
        matched.append(item)
    return matched


def result_stage_bytes(owner: Path) -> int:
    """임시 Stage·결과 조각과 소유 결과 파일의 실제 디스크 사용량."""
    try:
        size = owner.stat().st_size if owner.is_file() else 0
    except FileNotFoundError:
        size = 0
    for root in _owned_directories(owner):
        try:
            for entry in root.rglob("*"):
                if entry.is_symlink():
                    raise ValueError("Result Stage cannot contain symbolic links")
                try:
                    if entry.is_file():
                        size += entry.stat().st_size
                except FileNotFoundError:
                    # 임시 파일이 폴링 사이에 정상 삭제될 수 있다.
                    continue
        except FileNotFoundError:
            continue
    return size


def cleanup_result_stage(owner: Path) -> None:
    """종료·중단 Attempt의 남은 Stage·결과 조각 디렉터리를 제거한다."""
    for directory in _owned_directories(owner):
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            continue
