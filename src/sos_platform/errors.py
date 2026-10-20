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
        "CSV·Parquet 또는 구조화된 TSV·TXT 파일을 선택하세요.",
        "validation",
    ),
    spec(
        "SOS-UPLOAD-INVALID",
        "파일 형식 검사 실패",
        "파일이 비어 있거나 지원되는 형식의 데이터 구조·인코딩이 올바르지 않습니다.",
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
        "SOS-JOB-IDEMPOTENCY-REQUIRED",
        "제출 키 필요",
        "토큰 인증 환경에서 비동기 Job 제출에는 Idempotency-Key가 필요합니다.",
        "precondition",
    ),
    spec(
        "SOS-JOB-IDEMPOTENCY-CONFLICT",
        "제출 키 충돌",
        "동일한 제출 키로 다른 작업을 요청했습니다. 새 실행에는 새로운 키를 사용하세요.",
        "conflict",
    ),
    spec("SOS-API-INTERNAL", "요청 실패", "요청 처리 중 문제가 발생했습니다.", "internal"),
    spec(
        "SOS-API-DEPENDENCY",
        "외부 서비스 장애",
        "요청 처리에 필요한 서비스가 준비되지 않았습니다.",
        "dependency",
        "after_refresh",
    ),
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
        "SOS-ML-INPUT-INVALID",
        "학습 입력 검증 실패",
        "숫자형 Feature·결측값·값의 범위·학습 파라미터를 확인한 뒤 다시 제출하세요.",
        "validation",
    ),
    spec(
        "SOS-ML-TRAIN-CAPACITY",
        "학습 입력 실행 예산 초과",
        "요청한 학습 입력이 Worker 메모리 예산을 초과했습니다. 명시적인 부분집합 행 수를 선택하거나 실행 환경을 조정하세요.",
        "capacity",
    ),
    spec(
        "SOS-CACHE-CAPACITY",
        "로컬 캐시 용량 초과",
        "원본 파일을 읽기 위한 안전한 캐시 공간이 부족합니다.",
        "storage_capacity",
    ),
    spec(
        "SOS-EXECUTION-CAPACITY",
        "실행 자원 부족",
        "현재 실행 용량이 가득 찼습니다. 진행 중인 작업이 끝난 뒤 다시 시도하세요.",
        "capacity",
        "after_delay",
    ),
    spec(
        "SOS-DISCOVERY-CAPACITY",
        "탐색 실행 용량 초과",
        "동시 실행 가능 수를 초과했습니다. 잠시 후 다시 요청하세요.",
        "capacity",
        "after_delay",
    ),
    spec(
        "SOS-JOB-TIMEOUT",
        "실행 시간 초과",
        "작업이 허용된 실행 시간을 초과하여 종료됐습니다.",
        "execution",
    ),
    spec(
        "SOS-JOB-MEMORY-LIMIT",
        "실행 메모리 초과",
        "작업이 허용된 메모리 사용량을 초과하여 종료됐습니다.",
        "execution",
    ),
    spec(
        "SOS-JOB-INTERRUPTED",
        "실행 중단",
        "이전 실행이 중단됐습니다.",
        "execution",
        "after_refresh",
    ),
)


ERRORS = {item.code: item for item in DEFINITIONS}
if len(ERRORS) != len(DEFINITIONS):
    raise RuntimeError("Duplicate error code in the platform catalog")


class InvalidRequestError(ValueError):
    """클라이언트가 입력을 수정해 해결할 수 있는 명시적 오류."""


class RuntimeConfigurationError(ValueError):
    """서버 자원 설정의 모순·오입력. 클라이언트의 요청 검증 오류가 아니다."""


class WorkerOwnershipLost(RuntimeError):
    """Worker 잠금 세션 또는 확정 대상 Attempt의 소유권이 사라졌다."""


class PlatformError(Exception):
    """안전한 사용자 오류 메시지와 안정적인 오류 코드를 보유한다."""

    def __init__(self, code: str, *, stage: str | None = None):
        if code not in ERRORS:
            raise ValueError("Unregistered error code")
        self.code = code
        self.stage = stage
        super().__init__(ERRORS[code].message)
