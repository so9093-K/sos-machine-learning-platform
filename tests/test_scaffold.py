"""저장소 초기 구성의 기본 파일을 검증한다."""

from pathlib import Path


def test_foundation_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative_path in (
        "README.md",
        "AGENTS.md",
        "docs/vision/PLATFORM_IDENTITY.md",
        ".github/workflows/ci.yml",
    ):
        assert (root / relative_path).is_file(), relative_path
