"""오류 의미와 사용자 행동 정책의 단일 정본."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    title: str
    message: str
    category: str
    retry_policy: str = "never"


def spec(code: str, title: str, message: str, category: str, retry: str = "never") -> ErrorSpec:
    return ErrorSpec(code, title, message, category, retry)


DEFINITIONS = (
    spec("SOS-API-VALIDATION", "입력 검증 실패", "입력값을 확인하세요.", "validation"),
    spec(
        "SOS-UPLOAD-TOO-LARGE",
        "업로드 크기 초과",
        "파일 크기가 허용된 업로드 한도를 초과했습니다.",
        "upload_size",
    ),
    spec(
        "SOS-UPLOAD-FORMAT",
        "지원되지 않는 파일 형식",
        "CSV 또는 Parquet 파일을 선택하세요.",
        "validation",
    ),
    spec(
        "SOS-UPLOAD-INVALID",
        "파일 형식 검사 실패",
        "파일이 비어 있거나 올바른 CSV·Parquet 데이터가 아닙니다.",
        "validation",
    ),
    spec("SOS-API-NOT-FOUND", "리소스 부재", "요청한 리소스를 찾을 수 없습니다.", "not_found"),
    spec(
        "SOS-API-METHOD-NOT-ALLOWED",
        "메서드 미지원",
        "해당 HTTP 메서드를 지원하지 않습니다.",
        "validation",
    ),
    spec("SOS-API-CONFLICT", "상태 충돌", "현재 상태에서는 처리할 수 없습니다.", "conflict"),
    spec(
        "SOS-JOB-IDEMPOTENCY-CONFLICT",
        "제출 키 충돌",
        "동일한 제출 키로 다른 작업을 요청했습니다. 새 실행에는 새로운 키를 사용하세요.",
        "conflict",
    ),
    spec("SOS-API-INTERNAL", "요청 실패", "요청 처리 중 문제가 발생했습니다.", "internal"),
    spec("SOS-API-DEPENDENCY", "외부 서비스 장애", "요청 처리에 필요한 서비스가 준비되지 않았습니다.", "dependency", "after_refresh"),
    spec("SOS-AUTH-REQUIRED", "인증 필요", "인증이 필요합니다.", "authentication"),
    spec("SOS-AUTH-INVALID", "인증 실패", "인증 정보를 확인하세요.", "authentication"),
    spec("SOS-AUTH-DENIED", "접근 거부", "요청한 작업에 접근할 수 없습니다.", "authorization"),
    spec(
        "SOS-STORAGE-001",
        "저장소 장애",
        "저장소를 사용할 수 없습니다.",
        "dependency",
        "after_refresh",
    ),
    spec(
        "SOS-STORAGE-INSPECTION",
        "저장소 진단 장애",
        "저장소 상태를 확인할 수 없습니다.",
        "dependency",
        "after_refresh",
    ),
    spec(
        "SOS-ICEBERG-CANCEL-409",
        "Iceberg 취소 불가",
        "외부 커밋 여부를 보장할 수 없어 실행 중 취소할 수 없습니다.",
        "commit_uncertain",
    ),
    spec(
        "SOS-ICEBERG-UNCERTAIN",
        "Iceberg 커밋 확인 필요",
        "Snapshot 확인 전에는 다시 적재하지 마세요.",
        "commit_uncertain",
        "manual_verification_required",
    ),
    spec(
        "SOS-ICEBERG-RETAIN",
        "Snapshot 보존 확인 실패",
        "Snapshot 보호 여부를 확인할 수 없습니다.",
        "commit_uncertain",
        "manual_verification_required",
    ),
    spec("SOS-JOB-FAILED", "실행 실패", "작업 실행 중 오류가 발생했습니다.", "execution"),
    spec(
        "SOS-JOB-INTERRUPTED",
        "실행 중단",
        "이전 실행이 중단됐습니다.",
        "execution",
        "after_refresh",
    ),
)


CLI_CODES = (
    "SOS-LIFE-001",
    "SOS-CMD-001",
    "SOS-CONFIG-001",
    "SOS-CONFIG-002",
    "SOS-CONFIG-003",
    "SOS-CONFIG-004",
    "SOS-NET-001",
    "SOS-PROC-001",
    "SOS-PROC-002",
    "SOS-READY-001",
    "SOS-INFRA-001",
    "SOS-INFRA-002",
    "SOS-ENV-001",
    "SOS-ENV-002",
    "SOS-ENV-003",
    "SOS-ENV-004",
    "SOS-DB-001",
    "SOS-EXTERNAL-001",
    "SOS-CHECK-001",
    "SOS-CHECK-002",
)
ERRORS = {item.code: item for item in DEFINITIONS}
ERRORS.update(
    {
        code: spec(code, "개발 환경 진단", "make doctor 및 make logs를 확인하세요.", "cli")
        for code in CLI_CODES
        if code not in ERRORS
    }
)
if len(ERRORS) != len(DEFINITIONS) + len(
    [code for code in CLI_CODES if code not in {x.code for x in DEFINITIONS}]
):
    raise RuntimeError("Duplicate error code in the platform catalog")


class PlatformError(Exception):
    """안전한 사용자 오류 메시지와 안정적인 오류 코드를 보유한다."""

    def __init__(self, code: str, *, stage: str | None = None):
        if code not in ERRORS:
            raise ValueError("Unregistered error code")
        self.code = code
        self.stage = stage
        super().__init__(ERRORS[code].message)
