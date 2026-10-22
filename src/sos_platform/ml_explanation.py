"""Isolation Forest 실제 이상 점수의 개별 입력 민감도 설명.

Feature를 학습 데이터의 참조 중앙값으로 치환할 때 변화하는 점수를 기록한다.
SHAP 값·가법적 기여도·이상 원인 또는 인과효과를 주장하지 않는다.
"""

import math

import numpy as np

from .ml_decision import _verified_scoring_row
from .ml_runtime import load_trusted_isolation_forest


def explain_scored_row(store, spec: dict) -> dict:
    """보존된 Scoring Result의 한 행을 모델 학습 참조 중앙값으로 비교한다."""
    scoring = store.get_scoring_result(spec["scoring_result_id"])
    if scoring is None or scoring["owner_actor"] != spec["source_owner_actor"]:
        raise ValueError("Scoring Result is unavailable")
    model = store.get_model_revision(scoring["model_revision_id"])
    if model is None:
        raise ValueError("Model Revision is unavailable")
    reference = model["runtime"].get("feature_reference_medians")
    names = model["features"]
    if not isinstance(reference, list) or len(reference) != len(names):
        raise ValueError("Model training reference values are unavailable")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in reference):
        raise ValueError("Model reference values are invalid")
    selected = spec["row_index"]
    item = _verified_scoring_row(store, scoring, selected)
    struct = item["features"]
    original = np.asarray([struct[name] for name in names], dtype=np.float64)
    model_runtime = load_trusted_isolation_forest(model, store.home / "cache")
    saved_score = float(item["anomaly_score"])
    recomputed = model_runtime.score_anomalies(original.reshape(1, -1))[0]
    if not math.isclose(saved_score, recomputed, rel_tol=1e-9, abs_tol=1e-10):
        raise ValueError("Scoring Result and model output disagree")
    changed = np.tile(original, (len(names), 1))
    changed[np.arange(len(names)), np.arange(len(names))] = reference
    adjusted_scores = model_runtime.score_anomalies(changed)
    items = [
        {
            "feature": name,
            "actual_value": None if np.isnan(original[index]) else float(original[index]),
            "missing_imputed": bool(np.isnan(original[index])),
            "reference_median": float(reference[index]),
            "score_if_replaced": float(adjusted_scores[index]),
            "anomaly_score_delta": float(saved_score - adjusted_scores[index]),
        }
        for index, name in enumerate(names)
    ]
    result = {
        "scoring_result_id": scoring["id"],
        "model_revision_id": model["id"],
        "row_index": selected,
        "method": "median_reference_feature_replacement",
        "score": saved_score,
        "feature_sensitivities": items,
        "baseline_source": "model_training_feature_medians",
        "additive_attribution": False,
    }
    identity = scoring.get("score_summary", {}).get("observation_identity")
    if identity is not None:
        if (
            identity.get("kind") != "opaque_uuid4"
            or identity.get("dataset_id") != scoring["dataset_id"]
            or identity.get("dataset_version") != scoring["dataset_version"]
            or "observation_id" not in item
        ):
            raise ValueError("Scoring observation identity evidence is invalid")
        result["source_observation"] = {
            "dataset_id": scoring["dataset_id"],
            "dataset_version": scoring["dataset_version"],
            "id_column": identity["source_column"],
            "observation_id": item["observation_id"],
        }
    return result
