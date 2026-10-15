"""외부 수집기 없이도 동작하는 구조화 진단 이벤트.

허용된 식별자와 분류 필드만 기록하고 예외 원문·요청 Payload는 기록하지 않는다.
"""

import json
import logging
import re
from datetime import UTC, datetime

_LOGGER = logging.getLogger("sos.events")
_FIELDS = {
    "request_id",
    "job_id",
    "attempt_id",
    "dataset_id",
    "error_code",
    "stage",
    "outcome",
    "status",
    "exception_type",
    "kind",
}
_SAFE_VALUE = re.compile(r"[a-zA-Z0-9_.:/-]{1,128}\Z")


def emit(event: str, *, service: str, level: int = logging.INFO, **context: str | None) -> None:
    """사용자 입력과 비밀정보를 로그에 삽입하지 않는 이벤트를 출력한다."""
    if not _SAFE_VALUE.fullmatch(event) or not _SAFE_VALUE.fullmatch(service):
        raise ValueError("Invalid diagnostic event")
    if not _LOGGER.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(stream)
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.propagate = False
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": service,
        "event": event,
        "level": logging.getLevelName(level),
    }
    for name, value in context.items():
        if name not in _FIELDS:
            raise ValueError("Unregistered diagnostic attribute")
        if value is not None and _SAFE_VALUE.fullmatch(str(value)):
            record[name] = str(value)
    _LOGGER.log(level, json.dumps(record, ensure_ascii=False, separators=(",", ":")))
