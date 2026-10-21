"""Training 정렬의 임시 파일을 Attempt 단위로 격리·관측·정리한다."""

import os
import shutil
import stat
from pathlib import Path
from uuid import UUID


def training_stage_root(home: Path) -> Path:
    return home / "cache" / "ml-training-staging"


def training_stage_path(home: Path, attempt_id: str) -> Path:
    """유효한 Attempt UUID만 경로 구성에 사용한다."""
    return training_stage_root(home) / f"attempt-{UUID(attempt_id)}"


def training_stage_bytes(home: Path, attempt_id: str) -> int:
    """폴더 밖으로 나가지 않고 정렬 임시 파일의 실제 크기를 관찰한다."""
    root = training_stage_root(home)
    if root.is_symlink():
        raise OSError("Training stage root must not be a symlink")
    folder = training_stage_path(home, attempt_id)
    if folder.is_symlink():
        raise OSError("Training stage directory must not be a symlink")
    if not folder.exists():
        return 0

    def on_error(error: OSError) -> None:
        raise error

    total = 0
    for directory, _subdirs, filenames in os.walk(
        folder, followlinks=False, onerror=on_error
    ):
        for filename in filenames:
            try:
                info = (Path(directory) / filename).stat(follow_symlinks=False)
            except FileNotFoundError:
                continue  # Reader의 정상 정리와 Worker의 관측이 경합할 수 있다.
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def cleanup_training_stage(home: Path, attempt_id: str | None = None) -> None:
    """실행 종료 또는 단독 Worker 복구 시 Attempt 소유 디렉터리만 회수한다."""
    root = training_stage_root(home)
    if root.is_symlink():
        raise OSError("Training stage root must not be a symlink")
    if not root.exists():
        return
    for folder in root.iterdir():
        if not folder.name.startswith("attempt-") or folder.is_symlink():
            continue
        try:
            valid = UUID(folder.name.removeprefix("attempt-"))
        except ValueError:
            continue
        if folder.name != f"attempt-{valid}" or (
            attempt_id is not None and valid != UUID(attempt_id)
        ):
            continue
        if folder.is_dir():
            shutil.rmtree(folder)
