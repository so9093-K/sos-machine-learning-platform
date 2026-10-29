"""소량 관측치를 신뢰된 Worker 자식 프로세스에서 점수화한다."""

import math
import numpy as np

from .ml_input import ordered_playground_input
from .ml_runtime import load_trusted_isolation_forest

def run_playground_scoring(store, spec: dict) -> dict:
    """동일 모델 어댑터로 실제 점수를 산출한다. Dataset·Scoring Result를 만들지 않는다."""
    model = store.get_model_revision(spec["model_revision_id"])
    if not model or model["owner_actor"] != spec["model_owner_actor"]:
        raise ValueError("Playground model is unavailable or ownership changed")
    values = ordered_playground_input(model, spec["features"])
    adapter = load_trusted_isolation_forest(model, store.home / "cache")
    score = adapter.score_anomalies(np.array([values], dtype=np.float64))[0]
    if not math.isfinite(score):
        raise ValueError("Playground score is not finite")
    return {
        "model_revision_id": model["id"],
        "anomaly_score": float(score),
        "score_direction": "higher_is_more_anomalous",
        "score_scale": "negative_sklearn_score_samples",
        "feature_count": len(values),
    }
