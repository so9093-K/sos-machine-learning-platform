"""공개 데이터 가져오기: 준비한 Dataset의 의미와 원본 확인(고정 해시·캐시·이용 조건 판본)."""

import hashlib
import io
import math
import urllib.error
import zipfile
from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from sos_platform import public_datasets
from sos_platform.analysis_topics import NETWORK_FLOW_FEATURES
from sos_platform.errors import PlatformError
from sos_platform.public_datasets import SOURCES, fetch_archive, import_part, prepare_part


def _csv(path: Path, rows: list[tuple[str, str, int, float]]) -> Path:
    """(Timestamp, Label, Attempted Category, Flow Bytes/s) 행으로 원본 형식의 CSV를 만든다."""
    pl.DataFrame({
        "Timestamp": [row[0] for row in rows],
        **{name: [1.0] * len(rows) for name in NETWORK_FLOW_FEATURES},
        "Label": [row[1] for row in rows],
        "Attempted Category": [row[2] for row in rows],
    }).with_columns(pl.Series("Flow Bytes/s", [row[3] for row in rows])).write_csv(path)
    return path


def test_training_part_keeps_benign_flows_with_utc_time_and_missing_for_infinity(tmp_path):
    source = _csv(tmp_path / "monday.csv", [
        ("2017-07-03 11:56:34.157427", "BENIGN", -1, 10.0),
        ("2017-07-03 11:56:35", "BENIGN", -1, math.inf),
    ])
    record = prepare_part(source, "train", tmp_path / "train.parquet")
    train = pl.read_parquet(tmp_path / "train.parquet")
    assert train.columns == ["ts", *NETWORK_FLOW_FEATURES]
    assert str(train.schema["ts"]) == "Datetime(time_unit='us', time_zone='UTC')"
    assert train["Flow Bytes/s"].to_list() == [10.0, None]
    assert record["nonfinite_to_missing"] == {"Flow Bytes/s": 1}


def test_training_part_with_attack_flows_is_rejected(tmp_path):
    source = _csv(tmp_path / "monday.csv", [("2017-07-03 11:56:34", "DDoS", -1, 1.0)])
    with pytest.raises(ValueError, match="정상"):
        prepare_part(source, "train", tmp_path / "train.parquet")


def test_evaluation_part_excludes_attempted_flows_and_binarizes_labels(tmp_path):
    source = _csv(tmp_path / "friday.csv", [
        ("2017-07-07 10:00:00", "BENIGN", -1, 1.0),
        ("2017-07-07 10:00:01", "DDoS", -1, 2.0),
        ("2017-07-07 10:00:02", "Botnet - Attempted", 6, 3.0),
    ])
    record = prepare_part(source, "evaluation", tmp_path / "evaluation.parquet")
    evaluation = pl.read_parquet(tmp_path / "evaluation.parquet")
    assert evaluation.columns[:3] == ["ts", "label", "attack"]
    assert evaluation["label"].to_list() == [0, 1]
    assert record["excluded_attempted"] == {"Botnet - Attempted": 1}
    assert record["rows"] == 2


def test_source_without_topic_features_is_rejected(tmp_path):
    path = tmp_path / "tuesday.csv"
    pl.DataFrame({"Timestamp": ["2017-07-04 10:00:00"], "Label": ["BENIGN"],
                  "Attempted Category": [-1]}).write_csv(path)
    with pytest.raises(ValueError, match="필수 컬럼 없음"):
        prepare_part(path, "evaluation", tmp_path / "out.parquet")


def _archive(tmp_path: Path) -> tuple[bytes, object]:
    """월요일·수요일 CSV를 담은 zip과, 그 zip을 고정 해시로 가리키는 카탈로그 원본."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for member, rows in [
            ("monday.csv", [("2017-07-03 10:00:00", "BENIGN", -1, 1.0)]),
            ("wednesday.csv", [("2017-07-05 10:00:00", "BENIGN", -1, 1.0),
                               ("2017-07-05 10:00:01", "DoS Hulk", -1, 2.0)]),
        ]:
            bundle.write(_csv(tmp_path / member, rows), member)
    data = buffer.getvalue()
    source = replace(SOURCES["cic-ids2017-cns2022"], sha256=hashlib.sha256(data).hexdigest(),
                     size_bytes=len(data))
    return data, source


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_is_verified_then_reused_from_cache_without_network(tmp_path, monkeypatch):
    data, source = _archive(tmp_path)
    calls = []
    monkeypatch.setattr(public_datasets.urllib.request, "urlopen",
                        lambda url, timeout: calls.append(url) or _Response(data))
    cache = tmp_path / "cache"
    assert fetch_archive(source, cache).read_bytes() == data
    monkeypatch.setattr(public_datasets.urllib.request, "urlopen",
                        lambda url, timeout: pytest.fail("검증된 캐시가 있으면 다시 받지 않는다"))
    assert fetch_archive(source, cache).read_bytes() == data
    assert calls == [source.url]


@pytest.mark.parametrize("served", [b"different-bytes", b"x" * 10_000_000])
def test_download_with_another_hash_or_size_is_rejected_without_leaving_files(tmp_path, monkeypatch, served):
    _, source = _archive(tmp_path)
    monkeypatch.setattr(public_datasets.urllib.request, "urlopen", lambda url, timeout: _Response(served))
    cache = tmp_path / "cache"
    with pytest.raises(PlatformError) as caught:
        fetch_archive(source, cache)
    assert caught.value.code == "SOS-PUBLIC-DATA-MISMATCH"
    assert list(cache.iterdir()) == []


def test_unreachable_source_is_reported_as_unavailable(tmp_path, monkeypatch):
    _, source = _archive(tmp_path)

    def refuse(url, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(public_datasets.urllib.request, "urlopen", refuse)
    with pytest.raises(PlatformError) as caught:
        fetch_archive(source, tmp_path / "cache")
    assert caught.value.code == "SOS-PUBLIC-DATA-UNAVAILABLE"


def _spec(source, part="evaluation-wednesday", **override):
    return {"source_id": source.id, "part": part, "sha256": source.sha256,
            "terms_version": source.terms_version, "recipe_version": source.recipe_version, **override}


def test_import_prepares_the_requested_part_with_its_provenance(tmp_path, monkeypatch):
    data, source = _archive(tmp_path)
    monkeypatch.setitem(SOURCES, source.id, source)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{source.sha256}.zip").write_bytes(data)
    result = import_part(cache, _spec(source), tmp_path / "out.parquet")
    assert result["dataset_name"] == "cic-ids2017-cns2022-evaluation-wednesday.parquet"
    assert result["public_source"]["sha256"] == source.sha256
    assert result["attacks"] == {"BENIGN": 1, "DoS Hulk": 1}
    assert pl.read_parquet(tmp_path / "out.parquet")["label"].to_list() == [0, 1]


@pytest.mark.parametrize("override", [{"terms_version": "old"}, {"recipe_version": "0"}, {"part": "unknown"}])
def test_import_accepted_under_other_terms_or_recipe_is_not_run(tmp_path, monkeypatch, override):
    _, source = _archive(tmp_path)
    monkeypatch.setattr(public_datasets.urllib.request, "urlopen",
                        lambda url, timeout: pytest.fail("확인하지 않은 조건으로 원본을 받지 않는다"))
    with pytest.raises(PlatformError) as caught:
        import_part(tmp_path / "cache", _spec(SOURCES[source.id], **override), tmp_path / "out.parquet")
    assert caught.value.code == "SOS-PUBLIC-DATA-MISMATCH"
