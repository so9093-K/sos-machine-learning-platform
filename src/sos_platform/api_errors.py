"""공통 HTTP 오류 계약과 안전한 예외 경계."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .errors import ERRORS, InvalidRequestError, PlatformError, RuntimeConfigurationError
from .observability import emit


def validation_message(kind: str) -> str:
    """검증 유형만 번역하며 입력값·정규식·검증기 예외 원문은 공개하지 않는다."""
    messages = {
        "missing": "필수 입력값입니다.",
        "extra_forbidden": "허용되지 않는 입력 항목입니다.",
        "string_pattern_mismatch": "입력 형식이 올바르지 않습니다.",
        "string_too_short": "입력값이 최소 길이보다 짧습니다.",
        "string_too_long": "입력값이 최대 길이를 초과했습니다.",
        "greater_than": "허용된 최솟값보다 커야 합니다.",
        "greater_than_equal": "허용된 최솟값 이상이어야 합니다.",
        "less_than": "허용된 최댓값보다 작아야 합니다.",
        "less_than_equal": "허용된 최댓값 이하여야 합니다.",
        "multiple_of": "허용된 단위의 배수여야 합니다.",
        "finite_number": "유한한 수를 입력하세요.",
        "literal_error": "허용된 선택값을 입력하세요.",
        "enum": "허용된 선택값을 입력하세요.",
        "too_short": "입력 항목 수가 최소 개수보다 적습니다.",
        "too_long": "입력 항목 수가 최대 개수를 초과했습니다.",
        "json_invalid": "JSON 형식이 올바르지 않습니다.",
        "model_attributes_type": "입력 항목을 JSON 객체로 전달하세요.",
    }
    if kind in messages:
        return messages[kind]
    for prefix, message in (
        ("string_", "문자열을 입력하세요."),
        ("int_", "정수를 입력하세요."),
        ("float_", "숫자를 입력하세요."),
        ("bool_", "참 또는 거짓 값을 입력하세요."),
        ("list_", "목록을 입력하세요."),
        ("dict_", "JSON 객체를 입력하세요."),
    ):
        if kind.startswith(prefix):
            return message
    return "입력값을 확인하세요."


def problem_response(
    request: Request, code: str, status: int, detail: object, headers: dict | None = None
) -> JSONResponse:
    definition = ERRORS[code]
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status,
        headers={**(headers or {}), "X-Request-ID": request_id},
        content={
            "type": f"/problems/{code.lower()}",
            "title": definition.title,
            "status": status,
            "detail": detail,
            "code": code,
            "category": definition.category,
            "retry_policy": definition.retry_policy,
            "request_id": request_id,
            "instance": f"urn:uuid:{request_id}",
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    """전송 레이어의 오류 표현만 담당하며 비즈니스 상태는 바꾸지 않는다."""

    @app.exception_handler(HTTPException)
    async def handle_http(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and exc.detail.get("code") in ERRORS:
            code = exc.detail["code"]
        elif exc.status_code == 401:
            code = (
                "SOS-AUTH-REQUIRED"
                if exc.detail == ERRORS["SOS-AUTH-REQUIRED"].message
                else "SOS-AUTH-INVALID"
            )
        else:
            code = {
                403: "SOS-AUTH-DENIED",
                404: "SOS-API-NOT-FOUND",
                405: "SOS-API-METHOD-NOT-ALLOWED",
                409: "SOS-API-CONFLICT",
                503: "SOS-API-DEPENDENCY",
            }.get(exc.status_code, "SOS-API-VALIDATION")
        return problem_response(request, code, exc.status_code, exc.detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        # 입력 원본(input/context)을 내보내지 않고 검증 위치와 안전한 설명만 제공한다.
        fields = []
        for issue in exc.errors():
            kind = issue.get("type", "")
            fields.append(
                {
                    "loc": list(issue.get("loc", [])),
                    "type": kind,
                    "msg": validation_message(kind),
                }
            )
        return problem_response(request, "SOS-API-VALIDATION", 422, fields)

    @app.exception_handler(RuntimeConfigurationError)
    async def handle_runtime_configuration(request: Request, exc: RuntimeConfigurationError):
        # 서버의 구성 오류는 사용자 입력 422로 표현하거나 설정 원문을 공개하지 않는다.
        emit(
            "api.request.failed",
            service="sos-api",
            level=logging.ERROR,
            request_id=getattr(request.state, "request_id", None),
            error_code="SOS-API-INTERNAL",
            exception_type=type(exc).__name__,
        )
        return problem_response(
            request, "SOS-API-INTERNAL", 500, ERRORS["SOS-API-INTERNAL"].message
        )

    @app.exception_handler(InvalidRequestError)
    async def handle_invalid_input(request: Request, exc: InvalidRequestError):
        # 명시적으로 입력 원인을 분류한 호출자만 안전한 상세를 공개한다.
        return problem_response(request, "SOS-API-VALIDATION", 422, str(exc))

    @app.exception_handler(ValueError)
    async def handle_unexpected_value(request: Request, exc: ValueError):
        # 라이브러리·서버 내부 ValueError는 입력 오류라고 추정하지 않는다.
        emit(
            "api.request.failed",
            service="sos-api",
            level=logging.ERROR,
            request_id=getattr(request.state, "request_id", None),
            error_code="SOS-API-INTERNAL",
            exception_type=type(exc).__name__,
        )
        return problem_response(
            request, "SOS-API-INTERNAL", 500, ERRORS["SOS-API-INTERNAL"].message
        )

    @app.exception_handler(PlatformError)
    async def handle_platform(request: Request, exc: PlatformError):
        entry = ERRORS[exc.code]
        status = {
            "upload_size": 413,
            "validation": 422,
            "not_found": 404,
            "conflict": 409,
            "commit_uncertain": 409,
            "dependency": 503,
            "capacity": 429,
            "storage_capacity": 507,
            "precondition": 428,
        }.get(entry.category, 500)
        return problem_response(request, exc.code, status, entry.message)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        emit(
            "api.request.failed",
            service="sos-api",
            level=logging.ERROR,
            request_id=getattr(request.state, "request_id", None),
            error_code="SOS-API-INTERNAL",
            exception_type=type(exc).__name__,
        )
        return problem_response(
            request, "SOS-API-INTERNAL", 500, ERRORS["SOS-API-INTERNAL"].message
        )
