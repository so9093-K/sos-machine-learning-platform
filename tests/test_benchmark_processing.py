"""데이터 처리 기준선 계측 도구가 실제 SOS Transform을 사용하는지 검증한다."""

import importlib.util
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_processing.py"
spec = importlib.util.spec_from_file_location("sos_benchmark_processing", source)
assert spec is not None and spec.loader is not None
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@pytest.mark.parametrize("scenario", ["row_local", "window"])
def test_benchmark_measures_real_platform_transform(scenario):
    result = benchmark.run_trial(120, 8, scenario)
    assert result["scenario"] == scenario
    assert result["input_rows"] == 120
    assert result["output_rows"] == 120
    assert result["groups"] == 8
    assert result["output_bytes_estimated"] > 0
    assert result["elapsed_ms"] >= 0
    if result["rss_before_bytes"] is not None:
        assert result["rss_after_bytes"] is not None
        assert result["process_highwater_rss_bytes"] >= result["rss_after_bytes"]


@pytest.mark.parametrize(
    ("rows", "groups", "scenario"),
    [(0, 1, "window"), (5_000_001, 1, "window"), (10, 11, "row_local"), (10, 2, "other")],
)
def test_benchmark_rejects_invalid_parameters(rows, groups, scenario):
    with pytest.raises(ValueError):
        benchmark.run_trial(rows, groups, scenario)
