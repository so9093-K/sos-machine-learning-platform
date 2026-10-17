"""문서 탐색 목차가 실제 ADR을 누락하지 않도록 검증한다."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "check_doc_links.py"
spec = importlib.util.spec_from_file_location("sos_doc_links", SOURCE)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_unindexed_adrs_are_detected_without_deleting_history(tmp_path):
    directory = tmp_path / "adr"
    directory.mkdir()
    (directory / "README.md").write_text(
        "- [0001 — 기준](0001-base.md)\n", encoding="utf-8"
    )
    (directory / "0001-base.md").write_text("# 0001\n", encoding="utf-8")
    (directory / "0002-new.md").write_text("# 0002\n", encoding="utf-8")
    assert checker.unindexed_adrs(directory) == ["0002-new.md"]
    with (directory / "README.md").open("a", encoding="utf-8") as index:
        index.write("- [0002 — 새 결정](0002-new.md)\n")
    assert checker.unindexed_adrs(directory) == []


def test_current_adr_index_contains_every_decision():
    assert checker.unindexed_adrs(ROOT / "docs" / "adr") == []
