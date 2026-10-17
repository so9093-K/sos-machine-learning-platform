"""공통 HTTP 오류 계약과 안전한 예외 경계."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .errors import ERRORS, InvalidRequestError, PlatformError, RuntimeConfigurationError
from .observability import emit


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
                if exc.detail == "Authentication required"
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
            safe_kind = kind.startswith(
                (
                    "missing",
                    "string_",
                    "int_",
                    "float_",
                    "bool_",
                    "list_",
                    "greater_",
                    "less_",
                    "literal_",
                    "extra_",
                )
            )
            fields.append(
                {
                    "loc": list(issue.get("loc", [])),
                    "type": kind,
                    "msg": issue.get("msg", "") if safe_kind else "입력값을 확인하세요.",
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
