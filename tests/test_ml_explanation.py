"""개별 설명은 실제 점수 변화에 기반하며 인과적 기여로 오인되지 않는다."""

from pathlib import Path

import polars as pl
import pytest

from sos_platform.ml_explanation import explain_scored_row
from sos_platform.ml_isolation_forest import train_isolation_forest


class LocalStore:
    home = Path("/tmp")

    def get_scoring_result(self, item):
        if item == "scoring":
            return {
                "id": "scoring", "owner_actor": "alice",
                "model_revision_id": "model",
            }
        return None

    def get_model_revision(self, item):
        if item == "model":
            return {
                "id": "model",
                "features": ["a", "b"],
                "runtime": {"feature_reference_medians": [1.5, 2.5]},
            }
        return None


def test_feature_replacement_is_numeric_not_shap_claim(monkeypatch):
    model = train_isolation_forest([[1, 2], [2, 3], [3, 2], [20, 30]])
    frame = pl.DataFrame({
        "row_index": [0], "anomaly_score": [model.score_anomalies([[20, 30]])[0]],
    }).with_columns(pl.DataFrame({"a": [20.0], "b": [30.0]}).to_struct("features"))
    monkeypatch.setattr("sos_platform.ml_explanation._verified_scoring", lambda store, src: frame)
    monkeypatch.setattr(
        "sos_platform.ml_explanation.load_trusted_isolation_forest",
        lambda revision, scratch: model,
    )
    result = explain_scored_row(LocalStore(), {
        "scoring_result_id": "scoring", "source_owner_actor": "alice",
        "row_index": 0,
    })
    assert result["additive_attribution"] is False
    assert result["baseline_source"] == "model_training_feature_medians"
    for feature in result["feature_sensitivities"]:
        assert feature["anomaly_score_delta"] == pytest.approx(
            result["score"] - feature["score_if_replaced"]
        )
    with pytest.raises(ValueError, match="out of bounds"):
        explain_scored_row(LocalStore(), {
            "scoring_result_id": "scoring", "source_owner_actor": "alice",
            "row_index": 1,
        })
