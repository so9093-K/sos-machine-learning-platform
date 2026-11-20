"""CNS2022 정정판 CIC-IDS2017을 네트워크 흐름 이상 탐지 주제의 생산 입력과 평가 Dataset으로 준비한다.

원본은 연구 목적으로 공개되며 인용을 요구한다. 재배포 조건이 명시되지 않았으므로 저장소·이미지에
넣지 않고, 사용자가 원본에서 받은 zip을 고정한 SHA-256으로 확인한 뒤 변환한다.

출력(`--output` 디렉터리):
- `train.parquet`: 정상 트래픽만 있는 월요일. `ts`(UTC)와 주제 Feature. Dagster 생산 Pipeline 입력.
- `evaluation-<요일>.parquet`: 화~금. `ts`, `label`(공격 1·정상 0), `attack`(원본 레이블), 주제 Feature.
  공격 시도가 효과 없이 끝난 'Attempted' 행은 정상·공격 어느 쪽으로도 단정할 수 없어 제외한다.
- `manifest.json`: 출처·해시·인용, 파일별 행 수·공격 유형·제외 행 수·무한값을 결측으로 둔 수.

흐름 통계의 무한값(지속 시간 0인 흐름의 초당 비율 등)은 임의의 수로 바꾸지 않고 결측으로 둔다.
결측은 모델 판본의 학습 중앙값으로 대체된다.
"""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sos_platform.analysis_topics import NETWORK_FLOW_FEATURES  # noqa: E402

SOURCE_URL = "https://intrusion-detection.distrinet-research.be/CNS2022/Datasets/CICIDS2017_improved.zip"
SOURCE_SHA256 = "97fdb91d339e2d8cf5627f981b831e5e7e400b981c58181c451a38fd03c48883"
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
CITATIONS = (
    "Sharafaldin, Habibi Lashkari, Ghorbani. Toward Generating a New Intrusion Detection Dataset and "
    "Intrusion Traffic Characterization. ICISSP 2018.",
    "Engelen, Rimmer, Joosen. Troubleshooting an Intrusion Detection Dataset: the CICIDS2017 Case Study. "
    "IEEE SPW (WTMC) 2021.",
    "Liu, Engelen, Lynar, Essam, Joosen. Error Prevalence in NIDS datasets: A Case Study on CIC-IDS-2017 "
    "and CSE-CIC-IDS-2018. IEEE CNS 2022.",
)
NOT_ATTEMPTED = -1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flows(path: Path) -> pl.LazyFrame:
    """원본 흐름 CSV. 관측 시각은 문서상 UTC이며 시간대 표기가 없어 UTC로 명시한다."""
    frame = pl.scan_csv(path, infer_schema_length=None)
    missing = [name for name in (*NETWORK_FLOW_FEATURES, "Timestamp", "Label", "Attempted Category")
               if name not in frame.collect_schema()]
    if missing:
        raise SystemExit(f"{path.name}: 필수 컬럼 없음 · {', '.join(missing[:8])}")
    schema = frame.collect_schema()
    finite = [
        (pl.when(pl.col(name).is_finite()).then(pl.col(name)).otherwise(None)
         if schema[name].is_float() else pl.col(name)).alias(name)
        for name in NETWORK_FLOW_FEATURES
    ]
    return frame.select(
        pl.col("Timestamp").str.to_datetime("%Y-%m-%d %H:%M:%S%.f", time_zone="UTC", strict=True).alias("ts"),
        pl.col("Label").alias("attack"),
        pl.col("Attempted Category").alias("attempted_category"),
        *finite,
        # 결측으로 바꾼 무한값 수를 근거로 남기기 위한 표시. 출력에는 쓰지 않는다.
        *[(~pl.col(name).is_finite()).fill_null(False).alias(f"__nonfinite__{name}")
          for name in NETWORK_FLOW_FEATURES if schema[name].is_float()],
    )


def prepare_day(source: Path, output: Path, day: str) -> dict:
    frame = flows(source).collect(engine="streaming")
    marks = [name for name in frame.columns if name.startswith("__nonfinite__")]
    nonfinite = {name.removeprefix("__nonfinite__"): int(frame[name].sum()) for name in marks}
    frame = frame.drop(marks)
    attempted = frame.filter(pl.col("attempted_category") != NOT_ATTEMPTED)
    kept = frame.filter(pl.col("attempted_category") == NOT_ATTEMPTED).drop("attempted_category")
    attacks = dict(kept.group_by("attack").len().sort("attack").iter_rows())
    record = {
        "rows": kept.height,
        "attacks": attacks,
        "excluded_attempted": dict(attempted.group_by("attack").len().sort("attack").iter_rows()),
        "nonfinite_to_missing": {name: count for name, count in nonfinite.items() if count},
    }
    if day == "monday":
        if set(attacks) != {"BENIGN"} or attempted.height:
            raise SystemExit("월요일 학습 입력에 정상(BENIGN)이 아닌 흐름이 있습니다.")
        kept.select("ts", *NETWORK_FLOW_FEATURES).write_parquet(output / "train.parquet")
        record["file"] = "train.parquet"
    else:
        name = f"evaluation-{day}.parquet"
        kept.select(
            "ts", (pl.col("attack") != "BENIGN").cast(pl.Int8).alias("label"), "attack",
            *NETWORK_FLOW_FEATURES,
        ).write_parquet(output / name)
        record["file"] = name
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help=f"원본 zip ({SOURCE_URL})")
    parser.add_argument("--output", type=Path, required=True, help="출력 디렉터리")
    args = parser.parse_args(argv)
    digest = sha256(args.source)
    if digest != SOURCE_SHA256:
        raise SystemExit(f"원본 SHA-256이 검증한 판본과 다릅니다: {digest}. 같은 정정판인지 확인하세요.")
    args.output.mkdir(parents=True, exist_ok=True)
    days = {}
    with TemporaryDirectory() as directory, zipfile.ZipFile(args.source) as archive:
        expected = {f"{day}.csv" for day in DAYS}
        if set(archive.namelist()) != expected:
            raise SystemExit(f"zip 구성이 다릅니다: {sorted(archive.namelist())}")
        for day in DAYS:
            path = Path(archive.extract(f"{day}.csv", directory))
            days[day] = prepare_day(path, args.output, day)
            path.unlink()
            print(f"{day}: {days[day]['rows']}행 → {days[day]['file']}", flush=True)
    manifest = {
        "source": {"url": SOURCE_URL, "sha256": digest, "citations": list(CITATIONS),
                   "terms": "연구 목적 공개·논문 인용 요구. 재배포 조건 명시 없음."},
        "topic": "network_flow_anomaly",
        "features": list(NETWORK_FLOW_FEATURES),
        "observation_time": "ts (원본 Timestamp를 UTC로 해석)",
        "label": "label = 1 if 원본 Label != BENIGN. Attempted 행은 제외.",
        "days": days,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {args.output / 'manifest.json'}")


if __name__ == "__main__":
    main()
