"""공개 흐름 데이터 준비의 의미: 정상만 학습 입력, Attempted 제외, 0/1 레이블, 무한값은 결측, UTC 시각."""

import math
import runpy
from pathlib import Path

import polars as pl
import pytest

recipe = runpy.run_path(str(Path(__file__).parents[1] / "scripts/prepare_cic_ids2017.py"))
FEATURES = recipe["NETWORK_FLOW_FEATURES"]


def _write(path: Path, rows: list[tuple[str, str, int, float]]) -> Path:
    """(Timestamp, Label, Attempted Category, Flow Bytes/s) 행으로 원본 형식의 CSV를 만든다."""
    frame = pl.DataFrame({
        "Timestamp": [row[0] for row in rows],
        **{name: [1.0] * len(rows) for name in FEATURES},
        "Label": [row[1] for row in rows],
        "Attempted Category": [row[2] for row in rows],
    }).with_columns(pl.Series("Flow Bytes/s", [row[3] for row in rows]))
    frame.write_csv(path)
    return path


def test_training_input_keeps_benign_flows_with_utc_time_and_missing_for_infinity(tmp_path):
    source = _write(tmp_path / "monday.csv", [
        ("2017-07-03 11:56:34.157427", "BENIGN", -1, 10.0),
        ("2017-07-03 11:56:35", "BENIGN", -1, math.inf),
    ])
    record = recipe["prepare_day"](source, tmp_path, "monday")
    train = pl.read_parquet(tmp_path / "train.parquet")
    assert train.columns == ["ts", *FEATURES]
    assert str(train.schema["ts"]) == "Datetime(time_unit='us', time_zone='UTC')"
    assert train["Flow Bytes/s"].to_list() == [10.0, None]
    assert record["nonfinite_to_missing"] == {"Flow Bytes/s": 1}


def test_monday_with_attack_flows_is_not_used_for_training(tmp_path):
    source = _write(tmp_path / "monday.csv", [("2017-07-03 11:56:34", "DDoS", -1, 1.0)])
    with pytest.raises(SystemExit, match="정상"):
        recipe["prepare_day"](source, tmp_path, "monday")


def test_evaluation_excludes_attempted_flows_and_binarizes_labels(tmp_path):
    source = _write(tmp_path / "friday.csv", [
        ("2017-07-07 10:00:00", "BENIGN", -1, 1.0),
        ("2017-07-07 10:00:01", "DDoS", -1, 2.0),
        ("2017-07-07 10:00:02", "Botnet - Attempted", 6, 3.0),
    ])
    record = recipe["prepare_day"](source, tmp_path, "friday")
    evaluation = pl.read_parquet(tmp_path / "evaluation-friday.parquet")
    assert evaluation["label"].to_list() == [0, 1]
    assert evaluation["attack"].to_list() == ["BENIGN", "DDoS"]
    assert record["excluded_attempted"] == {"Botnet - Attempted": 1}


def test_source_without_topic_features_is_rejected(tmp_path):
    path = tmp_path / "tuesday.csv"
    pl.DataFrame({"Timestamp": ["2017-07-04 10:00:00"], "Label": ["BENIGN"],
                  "Attempted Category": [-1]}).write_csv(path)
    with pytest.raises(SystemExit, match="필수 컬럼 없음"):
        recipe["prepare_day"](path, tmp_path, "tuesday")
