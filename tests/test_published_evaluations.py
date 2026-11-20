"""공개 평가 근거: 같은 판본·자기 평가만 공개하고, 소비자에게는 집계 요약만 보인다."""

import json

import pytest
from pydantic import ValidationError

from sos_platform.errors import InvalidRequestError
from sos_platform.schemas import PublishedEvaluationsRequest
from sos_platform.store_models import check_publishable_evaluation, published_evaluation


def test_only_evaluations_of_the_same_revision_owned_by_the_publisher_are_publishable():
    own = {"model_revision_id": "model-a", "owner_actor": "supplier"}
    check_publishable_evaluation(own, "model-a", "supplier", privileged=False)
    with pytest.raises(InvalidRequestError, match="이 모델 판본"):
        check_publishable_evaluation({**own, "model_revision_id": "model-b"}, "model-a", "supplier", False)
    with pytest.raises(InvalidRequestError, match="이 모델 판본"):
        check_publishable_evaluation(None, "model-a", "supplier", privileged=False)
    with pytest.raises(InvalidRequestError, match="이 계정이 만든"):
        check_publishable_evaluation({**own, "owner_actor": "someone-else"}, "model-a", "supplier", False)
    # 관리자는 다른 계정의 평가도 열람할 수 있으므로 공개할 수 있다.
    check_publishable_evaluation({**own, "owner_actor": "someone-else"}, "model-a", "admin", privileged=True)


def test_published_summary_keeps_aggregates_and_hides_data_references():
    row = {
        "evaluation_revision_id": "eval-1", "note": "수요일 DoS · 레이블 0/1", "published_at": "t2",
        "created_at": "t1", "label_coverage": "full",
        "decision_metrics": json.dumps({
            "score_count": 1000, "threshold": 0.62, "alert_rate": 0.05, "predicted_anomalies": 50,
            "evaluation_kind": "labeled", "true_positives": 40, "false_positives": 10,
            "false_negatives": 300, "true_negatives": 650, "precision": 0.8, "recall": 0.12, "f1": 0.2,
            "score_min": 0.3, "dataset_id": "private-dataset",
        }),
        "ranking_metrics": json.dumps({"average_precision": 0.77, "roc_auc": 0.94}),
        "policy_mode": "fixed", "policy_threshold": 0.62, "policy_alert_fraction": None,
    }
    summary = published_evaluation(row)
    assert summary["decision_metrics"]["true_positives"] == 40
    assert "dataset_id" not in summary["decision_metrics"] and "score_min" not in summary["decision_metrics"]
    assert summary["policy"] == {"mode": "fixed", "threshold": 0.62, "alert_fraction": None}
    assert summary["ranking_metrics"]["roc_auc"] == 0.94
    assert published_evaluation({**row, "policy_mode": None})["policy"] is None


def test_publication_request_requires_a_data_note_and_distinct_evaluations():
    item = {"evaluation_revision_id": "eval-1", "note": "수요일 DoS"}
    assert PublishedEvaluationsRequest(evaluations=[item]).evaluations[0].note == "수요일 DoS"
    with pytest.raises(ValidationError):
        PublishedEvaluationsRequest(evaluations=[{**item, "note": "   "}])
    with pytest.raises(ValidationError):
        PublishedEvaluationsRequest(evaluations=[item, item])
    assert PublishedEvaluationsRequest(evaluations=[]).evaluations == []
