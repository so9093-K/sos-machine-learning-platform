"""Model Revision 아티팩트의 SOS 저장 위치와 형식. 게시·복원·삭제가 같은 규칙을 쓴다.

모든 판본의 아티팩트는 그 판본을 게시한 SOS Job·Attempt 아래 불변 위치에 둔다. SOS가 학습한
판본은 신뢰된 Worker가 직접 쓴 joblib 묶음이고, MLflow에서 연결한 판본은 허용한 타입만 복원하는
skops 파일이다. API도 읽으므로 계산 의존성을 두지 않는다.
"""

from collections.abc import Mapping

JOBLIB = "joblib"
SKOPS = "skops"
_SUFFIXES = {JOBLIB: ".model.joblib", SKOPS: ".model.skops"}


def model_artifact_key(job_id: str, attempt_id: str, model_format: str) -> str:
    """판본을 게시한 Attempt의 아티팩트 객체 키."""
    return f"results/{job_id}/{attempt_id}{_SUFFIXES[model_format]}"


def revision_artifact_key(revision: Mapping) -> str:
    """판본 기록이 가리켜야 하는 아티팩트 객체 키. 기록된 형식만 따른다."""
    runtime = revision.get("runtime") or {}
    return model_artifact_key(revision["job_id"], revision["attempt_id"],
                              runtime.get("model_format", JOBLIB))


def is_model_artifact_key(key: str) -> bool:
    """SOS가 게시한 모델 아티팩트 위치다. 영구 삭제는 이 위치만 허용한다."""
    return key.startswith("results/") and key.endswith(tuple(_SUFFIXES.values()))
