"""Ray Serve 온라인 추론 애플리케이션. 서빙 Runtime 이미지에서만 import한다.

한 애플리케이션은 한 Model Revision을 상주시킨다. SOS 컨트롤러가 판본 기록을 인자로 넘기며,
복제본은 DB에 접근하지 않고 그 기록이 가리키는 S3 아티팩트만 복원한다.
"""

import platform
import tempfile
from pathlib import Path

import numpy as np
import sklearn
from ray import serve
from starlette.requests import Request

from .ml_inference import OUTPUT_TYPE, SCORE_DIRECTION, SCORE_SCALE, score_observations
from .ml_runtime import load_trusted_isolation_forest

MAX_ROWS = 100


@serve.deployment(name="model")
class ModelServer:
    """판본 기록의 학습 Runtime·SHA-256을 이 Runtime과 대조한 뒤에만 모델을 복원한다.

    대조에 실패하면 복제본이 시작되지 않고 Ray가 배포 실패로 보고한다.
    """

    def __init__(self, revision: dict, digest: str):
        self._revision = revision
        self._digest = digest
        scratch = Path(tempfile.mkdtemp(prefix="sos-serving-"))
        self._adapter = load_trusted_isolation_forest(revision, scratch)
        self._runtime = {"python": platform.python_version(), "numpy": np.__version__,
                         "scikit_learn": sklearn.__version__}

    async def __call__(self, request: Request) -> dict:
        """POST는 SOS API가 검증·정렬한 입력 행(결측은 null)을 계산한다.

        GET은 준비 확인이다. 이 복제본이 어떤 판본·설정 지문으로 시작됐는지 알려, 컨트롤러가
        이전 설정의 복제본을 새 판본의 준비로 오인하지 않게 한다.
        """
        if request.method == "GET":
            return {"model_revision_id": self._revision["id"], "digest": self._digest,
                    "runtime": self._runtime}
        body = await request.json()
        rows = body.get("rows") if isinstance(body, dict) else None
        width = len(self._revision["features"])
        if (not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS
                or any(not isinstance(row, list) or len(row) != width for row in rows)):
            raise ValueError("Inference rows do not match the model input signature")
        scores = score_observations(self._adapter, rows)
        return {
            "model_revision_id": self._revision["id"],
            "output_type": OUTPUT_TYPE,
            "score_direction": SCORE_DIRECTION,
            "score_scale": SCORE_SCALE,
            "outputs": [{"anomaly_score": score} for score in scores],
            "runtime": self._runtime,
        }


def build(args: dict):
    """Serve Config의 import_path가 가리키는 애플리케이션 생성기."""
    return ModelServer.bind(args["revision"], args["digest"])
