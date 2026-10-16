"""기술적 오류 분류는 허용된 예외 클래스와 코드만 사용한다."""

from botocore.exceptions import ClientError, EndpointConnectionError

from sos_platform.diagnostics import classify_failure


def test_diagnostic_classifier_does_not_persist_exception_message():
    secret = "postgresql://alice:password@internal/db"
    category, kind = classify_failure(RuntimeError(secret))
    assert (category, kind) == ("unclassified", "RuntimeError")
    assert secret not in (category + kind)


def test_object_store_failures_have_actionable_safe_categories():
    denial = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "password=secret"}},
        "PutObject",
    )
    assert classify_failure(denial) == ("permission", "ClientError")
    timeout = ClientError(
        {"Error": {"Code": "RequestTimeout", "Message": "token=secret"}}, "PutObject"
    )
    assert classify_failure(timeout) == ("timeout", "ClientError")
    assert classify_failure(EndpointConnectionError(endpoint_url="http://127.0.0.1:3")) == (
        "connection",
        "EndpointConnectionError",
    )


def test_unrecognized_provider_code_stays_unclassified_dependency():
    error = ClientError({"Error": {"Code": "Private-Secret-Not-Allowed"}}, "PutObject")
    assert classify_failure(error) == ("dependency", "ClientError")


def test_runtime_configuration_is_not_misclassified_as_user_input():
    from sos_platform.errors import RuntimeConfigurationError

    assert classify_failure(RuntimeConfigurationError("private configuration detail")) == (
        "configuration",
        "RuntimeConfigurationError",
    )
