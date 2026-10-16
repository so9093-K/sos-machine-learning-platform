"""Dataset와 Job의 키셋 페이지 커서를 위한 내부 공통 계약."""

import base64
import binascii
import json


def decode_cursor(token: str | None, resource: str) -> dict[str, str] | None:
    """기존 Base64url 키셋 커서를 검증한다. 공개 커서 형식은 변경하지 않는다."""
    if not token:
        return None
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        before = json.loads(raw)
        if (
            not isinstance(before, dict)
            or set(before) != {"created_at", "id"}
            or not all(isinstance(before[key], str) and before[key] for key in before)
            or len(before["created_at"]) > 80
            or len(before["id"]) != 36
        ):
            raise ValueError("Invalid cursor")
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid {resource} cursor") from exc
    return before


def page_from_rows(rows: list[dict], limit: int) -> dict:
    """LIMIT + 1 조회 결과를 기존 페이지 응답 형식으로 직렬화한다."""
    if len(rows) <= limit:
        return {"items": rows, "next_cursor": None}
    visible = rows[:limit]
    last = visible[-1]
    token = json.dumps({"created_at": last["created_at"], "id": last["id"]}, separators=(",", ":"))
    return {
        "items": visible,
        "next_cursor": base64.urlsafe_b64encode(token.encode()).decode().rstrip("="),
    }
