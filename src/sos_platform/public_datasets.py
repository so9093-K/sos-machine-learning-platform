"""검증한 공개 데이터 원본을 가져와 분석 주제의 일반 Dataset으로 준비한다.

임의 URL은 받지 않는다. 카탈로그에 올린 원본만 고정 URL·SHA-256·이용 조건 판본으로 가져오며, 가져오기는
사용자가 이용 조건을 확인하고 명시적으로 요청한 Job이다. 결과는 출처를 Job에 남긴 일반 Dataset이고 예제
경로가 아니다. 받은 원본은 Worker 캐시에 해시 이름으로 두어, 같은 원본의 다른 부분을 가져올 때 다시
받지 않는다. 캐시는 다시 받을 수 있는 사본이며 정본은 원본 위치와 고정 해시다.
"""

import hashlib
import os
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import polars as pl

from .analysis_topics import NETWORK_FLOW_FEATURES
from .errors import PlatformError

NOT_ATTEMPTED = -1
_CHUNK = 1_048_576


@dataclass(frozen=True)
class PublicPart:
    """원본에서 만드는 Dataset 하나. `role`이 train이면 정상만, evaluation이면 0/1 레이블을 가진다."""

    id: str
    title: str
    description: str
    member: str
    role: str


@dataclass(frozen=True)
class PublicSource:
    id: str
    title: str
    summary: str
    topic: str
    url: str
    sha256: str
    size_bytes: int
    terms: str
    # 이용 조건·준비 방법이 바뀌면 판본을 올린다. 가져오기 요청은 확인한 판본을 명시해야 한다.
    terms_version: str
    recipe_version: str
    citations: tuple[str, ...]
    parts: tuple[PublicPart, ...]

    def part(self, part_id: str) -> PublicPart | None:
        return next((item for item in self.parts if item.id == part_id), None)


_CIC = PublicSource(
    id="cic-ids2017-cns2022",
    title="CIC-IDS2017 정정판 (CNS2022)",
    summary="연구용 망에서 5일간 수집한 네트워크 흐름. 월요일은 정상 트래픽만, 화~금은 요일별 공격이 섞여 있다. "
            "원본 CICFlowMeter 추출·레이블 오류를 바로잡은 판본이다.",
    topic="network_flow_anomaly",
    url="https://intrusion-detection.distrinet-research.be/CNS2022/Datasets/CICIDS2017_improved.zip",
    sha256="97fdb91d339e2d8cf5627f981b831e5e7e400b981c58181c451a38fd03c48883",
    size_bytes=343_549_013,
    terms="연구 목적으로 공개되며 사용 시 아래 논문 인용을 요구한다. 재배포 조건은 명시되지 않았으므로 SOS는 "
          "원본을 포함하지 않고 원본 위치에서 받는다. 업무·상업 목적 사용은 원본 제공처의 조건을 따로 확인한다.",
    terms_version="2026-11-20",
    recipe_version="1",
    citations=(
        "Sharafaldin, Habibi Lashkari, Ghorbani. Toward Generating a New Intrusion Detection Dataset and "
        "Intrusion Traffic Characterization. ICISSP 2018.",
        "Engelen, Rimmer, Joosen. Troubleshooting an Intrusion Detection Dataset: the CICIDS2017 Case Study. "
        "IEEE SPW (WTMC) 2021.",
        "Liu, Engelen, Lynar, Essam, Joosen. Error Prevalence in NIDS datasets: A Case Study on CIC-IDS-2017 "
        "and CSE-CIC-IDS-2018. IEEE CNS 2022.",
    ),
    parts=(
        PublicPart("train-monday", "월요일 · 정상 트래픽", "학습·정상 기준. 공격 없음.", "monday.csv", "train"),
        PublicPart("evaluation-tuesday", "화요일 · 무차별 대입", "FTP·SSH 무차별 대입 공격 포함.",
                   "tuesday.csv", "evaluation"),
        PublicPart("evaluation-wednesday", "수요일 · 서비스 거부(DoS)", "DoS Hulk·GoldenEye·Slowloris 등. "
                   "공격이 흐름의 약 35%로 많다.", "wednesday.csv", "evaluation"),
        PublicPart("evaluation-thursday", "목요일 · 웹 공격·침투", "웹 공격·침투·포트 스캔 포함.",
                   "thursday.csv", "evaluation"),
        PublicPart("evaluation-friday", "금요일 · 봇넷·포트 스캔·DDoS", "봇넷·포트 스캔·DDoS 포함.",
                   "friday.csv", "evaluation"),
    ),
)

SOURCES: dict[str, PublicSource] = {_CIC.id: _CIC}


def catalog() -> list[dict]:
    """가져올 수 있는 원본과 부분. 화면·API가 이용 조건과 인용을 가져오기 전에 보인다."""
    return [{
        "id": item.id, "title": item.title, "summary": item.summary, "topic": item.topic,
        "url": item.url, "sha256": item.sha256, "size_bytes": item.size_bytes,
        "terms": item.terms, "terms_version": item.terms_version, "citations": list(item.citations),
        "parts": [{"id": part.id, "title": part.title, "description": part.description, "role": part.role}
                  for part in item.parts],
    } for item in SOURCES.values()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_archive(source: PublicSource, cache_dir: Path, *, timeout: float = 60) -> Path:
    """원본을 받아 고정 해시를 확인한다. 해시가 맞는 캐시가 있으면 다시 받지 않는다.

    Raises:
        PlatformError: 원본에 연결할 수 없거나(SOS-PUBLIC-DATA-UNAVAILABLE) 받은 원본의 크기·해시가
            검증한 판본과 다를 때(SOS-PUBLIC-DATA-MISMATCH). 다른 원본을 조용히 쓰지 않는다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{source.sha256}.zip"
    if target.is_file() and _sha256(target) == source.sha256:
        return target
    # 원본 크기보다 크게 받는 응답은 다른 파일이다. 디스크를 채우기 전에 멈춘다.
    limit = source.size_bytes
    with NamedTemporaryFile(dir=cache_dir, suffix=".partial", delete=False) as handle:
        partial = Path(handle.name)
        try:
            digest, size = hashlib.sha256(), 0
            with urllib.request.urlopen(source.url, timeout=timeout) as response:
                for chunk in iter(lambda: response.read(_CHUNK), b""):
                    size += len(chunk)
                    if size > limit:
                        raise PlatformError("SOS-PUBLIC-DATA-MISMATCH")
                    digest.update(chunk)
                    handle.write(chunk)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            partial.unlink(missing_ok=True)
            raise PlatformError("SOS-PUBLIC-DATA-UNAVAILABLE") from exc
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    if size != source.size_bytes or digest.hexdigest() != source.sha256:
        partial.unlink(missing_ok=True)
        raise PlatformError("SOS-PUBLIC-DATA-MISMATCH")
    os.replace(partial, target)
    return target


def _flows(path: Path) -> pl.LazyFrame:
    """원본 흐름 CSV. 관측 시각은 문서상 UTC이며 시간대 표기가 없어 UTC로 명시한다."""
    frame = pl.scan_csv(path, infer_schema_length=None)
    schema = frame.collect_schema()
    missing = [name for name in (*NETWORK_FLOW_FEATURES, "Timestamp", "Label", "Attempted Category")
               if name not in schema]
    if missing:
        raise ValueError(f"{path.name}: 필수 컬럼 없음 · {', '.join(missing[:8])}")
    return frame.with_columns(
        pl.col("Timestamp").str.to_datetime("%Y-%m-%d %H:%M:%S%.f", time_zone="UTC", strict=True).alias("ts"),
        pl.col("Label").alias("attack"),
    )


def prepare_part(source: Path, role: str, output: Path) -> dict:
    """원본 흐름 CSV 하나를 주제 입력 Dataset으로 바꾼다. 입력 크기와 관계없이 스트리밍으로 처리한다.

    - 효과 없이 끝난 공격 시도(Attempted Category != -1)는 정상·공격 어느 쪽으로도 단정할 수 없어 뺀다.
    - 흐름 통계의 무한값(지속 시간 0인 흐름의 초당 비율 등)은 임의의 수로 바꾸지 않고 결측으로 둔다.
    - `train`은 정상만 허용한다. `evaluation`은 `label`(공격 1·정상 0)과 원본 레이블 `attack`을 남긴다.

    Returns:
        행 수·공격 유형별 수·제외한 시도·결측으로 둔 무한값 수. Job 결과와 manifest에 남긴다.
    """
    flows = _flows(source)
    schema = flows.collect_schema()
    floats = [name for name in NETWORK_FLOW_FEATURES if schema[name].is_float()]
    attempted = pl.col("Attempted Category") != NOT_ATTEMPTED
    stats = flows.select(
        pl.len().alias("rows"),
        attempted.sum().alias("excluded"),
        *[(~pl.col(name).is_finite()).fill_null(False).sum().alias(name) for name in floats],
    ).collect(engine="streaming").row(0, named=True)
    kept = flows.filter(~attempted)
    attacks = dict(kept.group_by("attack").len().sort("attack").collect(engine="streaming").iter_rows())
    excluded = dict(flows.filter(attempted).group_by("attack").len().sort("attack")
                    .collect(engine="streaming").iter_rows())
    if role == "train" and (set(attacks) - {"BENIGN"} or excluded):
        raise ValueError("학습 입력에 정상(BENIGN)이 아닌 흐름이 있습니다.")
    finite = [
        (pl.when(pl.col(name).is_finite()).then(pl.col(name)).otherwise(None) if name in floats
         else pl.col(name)).alias(name)
        for name in NETWORK_FLOW_FEATURES
    ]
    labels = [] if role == "train" else [
        (pl.col("attack") != "BENIGN").cast(pl.Int8).alias("label"), pl.col("attack"),
    ]
    kept.select(pl.col("ts"), *labels, *finite).sink_parquet(output)
    return {
        "rows": sum(attacks.values()),
        "attacks": attacks,
        "excluded_attempted": excluded,
        "nonfinite_to_missing": {name: stats[name] for name in floats if stats[name]},
    }


def import_part(cache_dir: Path, spec: dict, output: Path) -> dict:
    """가져오기 Job 하나: 원본 확인 → 부분 추출 → 준비. 접수 후 카탈로그가 바뀌었으면 실행하지 않는다."""
    source = SOURCES.get(spec.get("source_id", ""))
    part = source.part(spec.get("part", "")) if source else None
    if (source is None or part is None or spec.get("sha256") != source.sha256
            or spec.get("terms_version") != source.terms_version
            or spec.get("recipe_version") != source.recipe_version):
        raise PlatformError("SOS-PUBLIC-DATA-MISMATCH")
    archive = fetch_archive(source, cache_dir)
    with TemporaryDirectory(dir=cache_dir) as directory, zipfile.ZipFile(archive) as bundle:
        if part.member not in bundle.namelist():
            raise PlatformError("SOS-PUBLIC-DATA-MISMATCH")
        extracted = Path(bundle.extract(part.member, directory))
        record = prepare_part(extracted, part.role, output)
    return {
        "dataset_name": f"{source.id}-{part.id}.parquet",
        "public_source": {
            "id": source.id, "title": source.title, "part": part.id, "part_title": part.title,
            "role": part.role, "url": source.url, "sha256": source.sha256,
            "terms_version": source.terms_version, "recipe_version": source.recipe_version,
            "citations": list(source.citations), "topic": source.topic,
        },
        **record,
    }
