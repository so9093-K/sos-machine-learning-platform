"""íë«í¼ ìë¹ì¤ êµ¬ì±ê³¼ ì¸ë¶ ì°ê²° ì¤ì ì ê´ë¦¬íë¤."""

import os
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class Settings:
    """ì¤í ìì¹ì ë¬´ê´í ë°ì´í°Â·ì¤í ë¦¬ì§ ì°ê²° ì¤ì ."""

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
            s3_endpoint=os.getenv("SOS_S3_ENDPOINT", "http://127.0.0.1:9605"),
            s3_access_key=os.getenv("SOS_S3_ACCESS_KEY", "sos-dev"),
            s3_secret_key=os.getenv("SOS_S3_SECRET_KEY", "sos-dev-secret"),
            s3_bucket=os.getenv("SOS_S3_BUCKET", "sos-data"),
            s3_region=os.getenv("SOS_S3_REGION", "us-east-1"),
        )
