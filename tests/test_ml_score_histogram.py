"""불변 Train·Scoring 점수의 집계 구간이 실제 결과와 일치하는지 검증한다."""
import math

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sos_platform.ml_anomaly import histogram_edges, score_histogram
from sos_platform.ml_scoring import histogram_from_scoring_file


def test_train_histogram_is_exact_and_right_endpoint_inclusive():
    values = [0.0, 0.25, 0.5, 0.5, 1.0]
    result = score_histogram(values, bins=2)
    assert result["method"] == "equal_width_exact_v1"
    assert result["bin_edges"] == [0.0, 0.5, 1.0]
    assert result["bin_counts"] == [2, 3]
    assert result["score_count"] == len(values)
    assert not result["constant_scores"]


def test_constant_scores_keep_exact_count_and_use_display_padding():
    result = score_histogram([0.4, 0.4, 0.4], bins=8)
    assert result["constant_scores"]
    assert sum(result["bin_counts"]) == 3
    assert result["bin_edges"][0] < 0.4 < result["bin_edges"][-1]
    assert result["bin_counts"].count(3) == 1


def test_histogram_rejects_invalid_count_and_nonfinite_values():
    with pytest.raises(ValueError):
        histogram_edges(0.0, 1.0, bins=0)
    with pytest.raises(ValueError):
        histogram_edges(math.inf, 1.0)
    with pytest.raises(ValueError):
        score_histogram([math.nan])


def test_scoring_histogram_reads_full_parquet_and_matches_train_logic(tmp_path):
    values = [0.0, 0.25, 0.5, 0.5, 1.0]
    path = tmp_path / "scores.parquet"
    pq.write_table(pa.table({"anomaly_score": values}), path)
    expected = score_histogram(values, bins=2)
    actual = histogram_from_scoring_file(
        path, low=0.0, high=1.0, expected_rows=len(values), bins=2,
    )
    assert actual == expected
    with pytest.raises(ValueError, match="score count"):
        histogram_from_scoring_file(
            path, low=0.0, high=1.0, expected_rows=10, bins=2,
        )


def test_nearly_identical_floating_scores_keep_strict_edges_and_exact_counts(tmp_path):
    low = 0.5
    high = math.nextafter(low, math.inf)
    values = [low] * 13 + [high] * 7
    trained = score_histogram(values)
    edges = trained["bin_edges"]
    assert all(b > a for a, b in zip(edges, edges[1:]))
    assert sum(trained["bin_counts"]) == 20
    path = tmp_path / "close.parquet"
    pq.write_table(pa.table({"anomaly_score": values}), path)
    scored = histogram_from_scoring_file(path, low=low, high=high, expected_rows=20)
    assert trained == scored


def test_scoring_histogram_constant_scores(tmp_path):
    values = [0.4] * 17
    path = tmp_path / "scores.parquet"
    pq.write_table(pa.table({"anomaly_score": values}), path)
    result = histogram_from_scoring_file(
        path, low=0.4, high=0.4, expected_rows=len(values), bins=8,
    )
    assert result["constant_scores"]
    assert sum(result["bin_counts"]) == len(values)
    assert result["bin_counts"] == score_histogram(values, bins=8)["bin_counts"]
