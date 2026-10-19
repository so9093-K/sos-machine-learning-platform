"""Scoring Result 재사용에 기반한 Policy·Evaluation의 독립 실행 검증."""

import polars as pl
import pytest

from sos_platform.ml_decision import apply_scoring_policy, evaluate_scoring_policy


class LocalResults:
    home = None

    def __init__(self):
        self.scoring = {
            "id": "score1",
            "owner_actor": "researcher",
            "ranking_metrics": {"average_precision": 0.75},
        }
        self.policy = {
            "id": "policy1",
            "scoring_result_id": "score1",
            "threshold": 0.5,
        }

    def get_scoring_result(self, item):
        return self.scoring if item == "score1" else None

    def get_decision_policy_revision(self, item):
        return self.policy if item == "policy1" else None


def _input():
    return pl.DataFrame({
        "row_index": [0, 1, 2, 3],
        "anomaly_score": [0.1, 0.2, 0.8, 0.9],
        "label": [0, 0, 1, 1],
    })


def test_policies_use_existing_scores_not_model_fit(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_decision._verified_scoring", lambda store, score: _input())
    store = LocalResults()
    fixed = apply_scoring_policy(store, {
        "scoring_result_id": "score1", "source_owner_actor": "researcher",
        "mode": "fixed", "threshold": 0.5,
    })
    quantile = apply_scoring_policy(store, {
        "scoring_result_id": "score1", "source_owner_actor": "researcher",
        "mode": "quantile", "alert_fraction": 0.25,
    })
    assert fixed["threshold"] == 0.5
    assert fixed["decision_summary"]["predicted_anomalies"] == 2
    assert quantile["threshold"] == pytest.approx(0.825)
    assert quantile["decision_summary"]["predicted_anomalies"] == 1
    assert "precision" not in fixed["decision_summary"]


def test_independent_evaluation_has_real_labeled_metrics(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_decision._verified_scoring", lambda store, score: _input())
    store = LocalResults()
    evaluated = evaluate_scoring_policy(store, {
        "scoring_result_id": "score1", "source_owner_actor": "researcher",
        "policy_revision_id": "policy1",
    })
    assert evaluated["label_coverage"] == "full"
    assert evaluated["decision_metrics"]["precision"] == 1.0
    assert evaluated["decision_metrics"]["recall"] == 1.0
    assert evaluated["ranking_metrics"]["average_precision"] == 0.75
    rank_only = evaluate_scoring_policy(store, {
        "scoring_result_id": "score1", "source_owner_actor": "researcher",
        "policy_revision_id": None,
    })
    assert "precision" not in rank_only["decision_metrics"]


def test_unlabeled_evaluation_does_not_invent_precision(monkeypatch):
    monkeypatch.setattr(
        "sos_platform.ml_decision._verified_scoring",
        lambda store, score: _input().drop("label"),
    )
    result = evaluate_scoring_policy(LocalResults(), {
        "scoring_result_id": "score1", "source_owner_actor": "researcher",
        "policy_revision_id": "policy1",
    })
    assert result["label_coverage"] == "none"
    assert "precision" not in result["decision_metrics"]


def test_policy_cannot_use_another_scoring_result(monkeypatch):
    monkeypatch.setattr("sos_platform.ml_decision._verified_scoring", lambda store, score: _input())
    store = LocalResults()
    store.policy = {**store.policy, "scoring_result_id": "score_other"}
    with pytest.raises(ValueError, match="does not belong"):
        evaluate_scoring_policy(store, {
            "scoring_result_id": "score1", "source_owner_actor": "researcher",
            "policy_revision_id": "policy1",
        })
