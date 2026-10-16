"""명시적 사용자 입력과 라이브러리·서버 내부 오류의 HTTP 구분."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos_platform.api_errors import register_error_handlers
from sos_platform.errors import InvalidRequestError, RuntimeConfigurationError


def test_explicit_request_error_is_422_but_internal_value_error_is_500():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/bad-input")
    def bad_input():
        raise InvalidRequestError("Unknown column: nonexistent")

    @app.get("/internal")
    def internal():
        raise ValueError("private connection string secret")

    @app.get("/configuration")
    def configuration():
        raise RuntimeConfigurationError("private environment secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        invalid = client.get("/bad-input")
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "SOS-API-VALIDATION"
        assert invalid.json()["detail"] == "Unknown column: nonexistent"
        for uri in ("/internal", "/configuration"):
            result = client.get(uri)
            assert result.status_code == 500
            assert result.json()["code"] == "SOS-API-INTERNAL"
            assert "private" not in result.text
            assert result.json()["request_id"] == result.headers["X-Request-ID"]
