"""외부 MLflow Tracking 연결은 운영자 환경 설정에서만 허용한다."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import RuntimeConfigurationError


@dataclass(frozen=True)
class MLflowSettings:
    endpoint: str
    experiment_id: str
    token: str | None
    timeout_seconds: int = 5

    @classmethod
    def from_env(cls) -> "MLflowSettings | None":
        url = os.getenv("SOS_MLFLOW_TRACKING_URI", "").strip()
        if not url:
            return None
        split = urlsplit(url)
        allow_http = os.getenv("SOS_MLFLOW_ALLOW_INSECURE_HTTP") == "true"
        local = split.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            split.scheme not in ("http", "https")
            or not split.hostname
            or split.username is not None
            or split.password is not None
            or split.path not in ("", "/")
            or split.query or split.fragment
            or (split.scheme == "http" and not (local or allow_http))
        ):
            raise RuntimeConfigurationError("Invalid MLflow Tracking endpoint")
        experiment = os.getenv("SOS_MLFLOW_EXPERIMENT_ID", "").strip()
        if not experiment.isdigit():
            raise RuntimeConfigurationError("SOS_MLFLOW_EXPERIMENT_ID must be numeric")
        return cls(
            endpoint=url.rstrip("/"),
            experiment_id=experiment,
            token=os.getenv("SOS_MLFLOW_TOKEN") or None,
        )
