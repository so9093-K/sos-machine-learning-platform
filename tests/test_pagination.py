"""Dataset·Job이 공유하는 기존 키셋 커서 계약을 검증한다."""

import pytest

from sos_platform.pagination import decode_cursor, page_from_rows


def test_cursor_round_trip_preserves_existing_contract():
    rows = [
        {"created_at": "2026-10-16T12:00:00+00:00", "id": str(n).zfill(36), "name": f"d{n}"}
        for n in range(3)
    ]
    page = page_from_rows(rows, 2)
    assert page["items"] == rows[:2]
    assert decode_cursor(page["next_cursor"], "Dataset") == {
        "created_at": rows[1]["created_at"],
        "id": rows[1]["id"],
    }
    assert decode_cursor(page["next_cursor"], "Job") == {
        "created_at": rows[1]["created_at"],
        "id": rows[1]["id"],
    }
    assert page_from_rows(rows[:2], 2) == {"items": rows[:2], "next_cursor": None}


def test_empty_cursor_and_empty_page():
    assert decode_cursor(None, "Dataset") is None
    assert decode_cursor("", "Dataset") is None
    assert page_from_rows([], 2) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("invalid", ["?", "bnVsbA", "e30", "W10", "MQ", "eyJpZCI6IjEifQ"])
def test_malformed_cursor_keeps_resource_specific_error(invalid):
    with pytest.raises(ValueError, match="Invalid Dataset cursor"):
        decode_cursor(invalid, "Dataset")
    with pytest.raises(ValueError, match="Invalid Job cursor"):
        decode_cursor(invalid, "Job")
