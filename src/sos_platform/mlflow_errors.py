"""MLflow Tracking·Registry 통합에서 공유하는 공개 가능한 오류 코드."""


class MLflowDeliveryError(Exception):
    """외부 시스템의 응답 본문이나 비밀정보를 직접 노출하지 않는다."""
