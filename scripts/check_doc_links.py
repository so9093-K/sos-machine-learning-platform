"""Markdown 내부의 상대 파일 참조가 실제 파일을 가리키는지 검사한다."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CHANGELOG.md"]
FILES += sorted((ROOT / "docs").rglob("*.md"))
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def unindexed_adrs(directory: Path) -> list[str]:
    """기존 ADR을 지우지 않고 인덱스 누락만 검사한다."""
    index = (directory / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"\]\((\d{4}-[^)#]+\.md)\)", index))
    return [
        path.name
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md"))
        if path.name not in listed
    ]


def main() -> None:
    """파일 참조와 ADR 인덱스 누락이 있으면 CI에서 실패하도록 한다."""
    missing = []
    for document in FILES:
        for match in LINK.finditer(document.read_text(encoding="utf-8")):
            target = match.group(1).split(" ", 1)[0]
            if target.startswith(("http:", "https:", "mailto:", "/", "#")):
                continue
            path = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if path and not (document.parent / path).exists():
                missing.append(f"{document.relative_to(ROOT)}: {target}")
    missing.extend(
        f"docs/adr/README.md: ADR not indexed: {name}"
        for name in unindexed_adrs(ROOT / "docs" / "adr")
    )
    if missing:
        raise SystemExit("Broken Markdown links or ADR index:\n" + "\n".join(missing))
    print(f"Validated relative links and ADR index in {len(FILES)} Markdown files")


if __name__ == "__main__":
    main()
