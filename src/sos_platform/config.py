"""플랫폼 서비스 구성과 외부 연결 설정을 관리한다."""

import os
from dataclasses import dataclass
from urllib.parse import quote_plus

from .errors import RuntimeConfigurationError


@dataclass(frozen=True)
class Settings:
    """실행 위치와 무관한 데이터·스토리지 연결 설정."""

    database_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("SOS_DATABASE_URL")
        if not database_url:
            user = quote_plus(os.getenv("SOS_DB_USER", "sos"))
            password = quote_plus(os.getenv("SOS_DB_PASSWORD", "sos-dev-only"))
            host = os.getenv("SOS_DB_HOST", "127.0.0.1")
            port = os.getenv("SOS_DB_PORT", "9603")
            database = os.getenv("SOS_DB_NAME", "sos")
            database_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
        return cls(
            database_url=database_url,
            s3_endpoint=os.getenv(
                "SOS_S3_ENDPOINT", f"http://127.0.0.1:{os.getenv('SOS_S3_PORT', '9605')}"
            ),
            s3_access_key=os.getenv("SOS_S3_ACCESS_KEY", "sos-dev"),
            s3_secret_key=os.getenv("SOS_S3_SECRET_KEY", "sos-dev-secret"),
            s3_bucket=os.getenv("SOS_S3_BUCKET", "sos-data"),
            s3_region=os.getenv("SOS_S3_REGION", "us-east-1"),
        )


@dataclass(frozen=True)
class StoragePolicy:
    """안전한 객체 관측·보존 정책. 객체 삭제는 제공하지 않는다."""

    grace_days: int = 7
    observation_hours: int = 24
    min_observations: int = 2
    page_size: int = 100

    def __post_init__(self) -> None:
        if self.grace_days < 7:
            raise RuntimeConfigurationError("SOS_OBJECT_GRACE_DAYS must be at least 7")
        if self.observation_hours < 24:
            raise RuntimeConfigurationError("SOS_OBJECT_OBSERVATION_HOURS must be at least 24")
        if self.min_observations < 2:
            raise RuntimeConfigurationError("SOS_OBJECT_MIN_OBSERVATIONS must be at least 2")
        if not 1 <= self.page_size <= 200:
            raise RuntimeConfigurationError("SOS_OBJECT_PAGE_SIZE must be between 1 and 200")

    @classmethod
    def from_env(cls) -> "StoragePolicy":
        """기본값을 제공하되 안전한 범위에서만 환경 설정을 허용한다."""
        try:
            return cls(
                grace_days=int(os.getenv("SOS_OBJECT_GRACE_DAYS", "7")),
                observation_hours=int(os.getenv("SOS_OBJECT_OBSERVATION_HOURS", "24")),
                min_observations=int(os.getenv("SOS_OBJECT_MIN_OBSERVATIONS", "2")),
                page_size=int(os.getenv("SOS_OBJECT_PAGE_SIZE", "100")),
            )
        except ValueError as exc:
            raise RuntimeConfigurationError(f"Invalid storage policy: {exc}") from exc

    def describe(self) -> dict:
        """API의 유효 설정과 기본값 대비 출처를 함께 제공한다."""
        keys = {
            "grace_days": "SOS_OBJECT_GRACE_DAYS",
            "observation_hours": "SOS_OBJECT_OBSERVATION_HOURS",
            "min_observations": "SOS_OBJECT_MIN_OBSERVATIONS",
            "page_size": "SOS_OBJECT_PAGE_SIZE",
        }
        return {
            "values": {key: getattr(self, key) for key in keys},
            "sources": {
                key: "environment" if name in os.environ else "default"
                for key, name in keys.items()
            },
            "automatic_deletion": False,
            "mode": "observation_only",
        }
