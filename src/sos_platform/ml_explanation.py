"""Isolation Forest의 점수 민감도와 선택적 Permutation SHAP 설명.

선택한 모델·Scoring의 실제 점수 함수를 설명하며 결과의 방법별 의미를 구별한다.
"""

import math

from importlib.metadata import version

import numpy as np

from .ml_decision import _verified_scoring_row
from .ml_playground import ordered_playground_input
from .ml_runtime import load_trusted_isolation_forest


def explain_scored_row(store, spec: dict) -> dict:
    """보존된 점수 결과의 한 행을 명시적으로 선택한 방법으로 설명한다.

    Args:
        store: 검증된 모델/Scoring 판본 및 아티팩트 접근자.
        spec: 설명 대상, 소유자, 결과 행 번호와 설명 method.

    Returns:
        dict: 불변 설명 결과로 게시할 방법별 실제 계산 근거.

    Raises:
        ValueError: 입력·판본·결과 불일치 또는 SHAP 설명 검증 실패.
    """
    method = spec.get("method", "median_reference_feature_replacement")
    if method not in ("median_reference_feature_replacement", "permutation_shap_median_v1"):
        raise ValueError("Unsupported explanation method")
    if spec.get("playground_job_id"):
        return explain_playground_job(store, spec)
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
    if method == "permutation_shap_median_v1":
        result = _permutation_shap_result(
            scoring["id"], model, selected, original, reference, saved_score, model_runtime
        )
    else:
        result = _median_reference_result(
            model, original, reference, saved_score, model_runtime,
            scoring_result_id=scoring["id"], row_index=selected,
        )
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



def _median_reference_result(
    model: dict, original: np.ndarray, reference: list[float],
    saved_score: float, model_runtime, *,
    scoring_result_id: str | None = None, row_index: int | None = None,
) -> dict:
    """원본 점수에서 Feature 하나씩 학습 중앙값으로 바꾼 실제 반응을 계산한다."""
    names = model["features"]
    changed = np.tile(original, (len(names), 1))
    changed[np.arange(len(names)), np.arange(len(names))] = reference
    adjusted_scores = model_runtime.score_anomalies(changed)
    return {
        "scoring_result_id": scoring_result_id,
        "model_revision_id": model["id"],
        "row_index": row_index,
        "method": "median_reference_feature_replacement",
        "score": saved_score,
        "feature_sensitivities": [
            {
                "feature": name,
                "actual_value": None if np.isnan(original[index]) else float(original[index]),
                "missing_imputed": bool(np.isnan(original[index])),
                "reference_median": float(reference[index]),
                "score_if_replaced": float(adjusted_scores[index]),
                "anomaly_score_delta": float(saved_score - adjusted_scores[index]),
            }
            for index, name in enumerate(names)
        ],
        "baseline_source": "model_training_feature_medians",
        "additive_attribution": False,
    }


def explain_playground_job(store, spec: dict) -> dict:
    """성공한 독립 점수화 Job의 불변 입력·실제 점수로 설명을 계산한다.

    화면의 편집값 대신 원본 Job을 다시 조회하며, 출처·모델·점수 일치성을 검증한다.
    """
    source = store.get_job(spec["playground_job_id"])
    if (
        source is None or source["kind"] != "ml_playground"
        or source["status"] != "Succeeded"
        or source["owner_actor"] != spec["source_owner_actor"]
    ):
        raise ValueError("Playground explanation source is unavailable")
    model = store.get_model_revision(source["spec"]["model_revision_id"])
    if (
        model is None or model["owner_actor"] != source["spec"]["model_owner_actor"]
        or model["lifecycle_state"] != "Active"
    ):
        raise ValueError("Playground explanation model is unavailable")
    reference = model["runtime"].get("feature_reference_medians")
    names = model["features"]
    if not isinstance(reference, list) or len(reference) != len(names):
        raise ValueError("Model training reference values are unavailable")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in reference):
        raise ValueError("Model reference values are invalid")

    original = np.asarray(
        ordered_playground_input(model, source["spec"]["features"]), dtype=np.float64
    )
    saved_result = source.get("result")
    if not isinstance(saved_result, dict) or (
        saved_result.get("model_revision_id") != model["id"]
        or saved_result.get("score_direction") != "higher_is_more_anomalous"
        or saved_result.get("score_scale") != "negative_sklearn_score_samples"
        or saved_result.get("feature_count") != len(names)
    ):
        raise ValueError("Playground score provenance and model revision disagree")
    score = saved_result.get("anomaly_score")
    if type(score) not in (int, float) or not math.isfinite(score):
        raise ValueError("Playground stored model score is unavailable")
    model_runtime = load_trusted_isolation_forest(model, store.home / "cache")
    recalculated = float(model_runtime.score_anomalies(original.reshape(1, -1))[0])
    if not math.isclose(float(score), recalculated, rel_tol=1e-9, abs_tol=1e-10):
        raise ValueError("Playground result and model output disagree")

    method = spec.get("method", "median_reference_feature_replacement")
    if method == "permutation_shap_median_v1":
        result = _permutation_shap_result(
            None, model, None, original, reference, float(score), model_runtime
        )
    elif method == "median_reference_feature_replacement":
        result = _median_reference_result(
            model, original, reference, float(score), model_runtime
        )
    else:
        raise ValueError("Unsupported explanation method")
    return {**result, "playground_job_id": source["id"]}


def _permutation_shap_result(
    scoring_result_id: str | None, model: dict, row_index: int | None, original: np.ndarray,
    reference: list[float], saved_score: float, model_runtime,
) -> dict:
    """고정 Train 중앙값 기준에서 실제 SOS 점수의 Permutation SHAP 값을 계산한다.

    SHAP masker의 단일 합성 배경 행과 Seed/평가 횟수를 고정한다.
    이 배경은 Train의 실제 관측 행 또는 전체 Train 분포를 대표하지 않는다.
    """
    import shap

    n_features = len(model["features"])
    if not 1 <= n_features <= 64:
        raise ValueError("Permutation SHAP supports between 1 and 64 features")
    # 신뢰된 모델 어댑터의 결측치 전처리와 점수 방향을 그대로 따른다.
    background = np.asarray(reference, dtype=np.float64).reshape(1, n_features)
    def score_fn(matrix: np.ndarray) -> np.ndarray:
        return np.asarray(model_runtime.score_anomalies(matrix), dtype=np.float64)

    seed = 42
    rounds = 3
    evaluations = rounds * (2 * n_features + 1)
    explainer = shap.PermutationExplainer(score_fn, background, seed=seed)
    explanation = explainer(original.reshape(1, n_features), max_evals=evaluations)
    values = np.asarray(explanation.values, dtype=np.float64)
    base = np.asarray(explanation.base_values, dtype=np.float64)
    if values.shape != (1, n_features) or base.size != 1:
        raise ValueError("Unexpected Permutation SHAP output shape")
    contributions = values[0]
    baseline_score = float(base.reshape(-1)[0])
    reconstructed_score = float(baseline_score + math.fsum(contributions.tolist()))
    if not np.isfinite(contributions).all() or not math.isfinite(baseline_score):
        raise ValueError("Non-finite Permutation SHAP explanation")
    residual = saved_score - reconstructed_score
    if not math.isclose(saved_score, reconstructed_score, abs_tol=1e-7, rel_tol=1e-7):
        raise ValueError("Permutation SHAP does not reconstruct SOS anomaly score")
    return {
        "scoring_result_id": scoring_result_id,
        "model_revision_id": model["id"],
        "row_index": row_index,
        "method": "permutation_shap_median_v1",
        "score": saved_score,
        "baseline_score": baseline_score,
        "reconstructed_score": reconstructed_score,
        "additivity_residual": residual,
        "model_output": "negative_sklearn_score_samples",
        "baseline_source": "model_training_feature_medians_single_synthetic_row",
        "baseline_row_count": 1,
        "sampling_seed": seed,
        "permutation_rounds": rounds,
        "max_evals": evaluations,
        "shap_version": version("shap"),
        "additive_attribution": True,
        "feature_contributions": [
            {
                "feature": name,
                "actual_value": None if np.isnan(original[index]) else float(original[index]),
                "missing_imputed": bool(np.isnan(original[index])),
                "reference_median": float(reference[index]),
                "shap_value": float(contributions[index]),
            }
            for index, name in enumerate(model["features"])
        ],
        "limitations": (
            "Feature contributions explain the SOS score relative to one synthetic "
            "training-median reference, not anomaly causes or incident probability."
        ),
    }
