"""안전한 실행 실패 분류. 예외 원문·입력 데이터·자격 증명을 기록하지 않는다."""

import re

from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from .errors import ERRORS, PlatformError, RuntimeConfigurationError

_REMOTE_CODES = {
    "AccessDenied": "permission",
    "InvalidAccessKeyId": "permission",
    "ExpiredToken": "permission",
    "SignatureDoesNotMatch": "permission",
    "NoSuchBucket": "not_found",
    "NoSuchKey": "not_found",
    "SlowDown": "throttled",
    "RequestTimeout": "timeout",
    "ServiceUnavailable": "dependency",
    "InternalError": "dependency",
}
_SAFE_EXCEPTION = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def classify_failure(exc: Exception) -> tuple[str, str]:
    """분류 가능한 예외 계층만 인식하고 나머지는 원인 미확정으로 보존한다."""
    name = type(exc).__name__
    exception_type = name if _SAFE_EXCEPTION.fullmatch(name) else "Exception"
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return _REMOTE_CODES.get(code, "dependency"), exception_type
    if isinstance(exc, (EndpointConnectionError, OperationalError)):
        return "connection", exception_type
    if isinstance(exc, TimeoutError):
        return "timeout", exception_type
    if isinstance(exc, IntegrityError):
        return "database_constraint", exception_type
    if isinstance(exc, RuntimeConfigurationError):
        return "configuration", exception_type
    if isinstance(exc, PlatformError):
        return ERRORS[exc.code].category, exception_type
    if isinstance(exc, (DataError, ValueError)):
        return "invalid_input", exception_type
    if isinstance(exc, OSError):
        return "io", exception_type
    return "unclassified", exception_type
