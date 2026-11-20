"""CIC-IDS2017 정정판(CNS2022)을 네트워크 흐름 주제의 생산 입력과 평가 Dataset 파일로 준비한다.

SOS의 '공개 데이터 가져오기'(`sos_platform.public_datasets`)와 같은 준비 함수를 쓴다. 이 스크립트는
Dagster 생산 Pipeline 입력처럼 SOS 밖에서 파일이 필요할 때 쓴다. 원본은 저장소·이미지에 넣지 않고,
원본 위치에서 받은 zip을 고정한 SHA-256으로 확인한 뒤 변환한다.

출력(`--output` 디렉터리): `train.parquet`(정상 월요일, `ts`·입력 Feature), `evaluation-<요일>.parquet`
(`ts`·`label`·`attack`·입력 Feature, Attempted 제외), 출처·인용·행 수·제외 수의 `manifest.json`.
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sos_platform.analysis_topics import NETWORK_FLOW_FEATURES  # noqa: E402
from sos_platform.public_datasets import SOURCES, _sha256, prepare_part  # noqa: E402

SOURCE = SOURCES["cic-ids2017-cns2022"]


def output_name(part_id: str) -> str:
    return "train.parquet" if part_id.startswith("train-") else f"{part_id}.parquet"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help=f"원본 zip ({SOURCE.url})")
    parser.add_argument("--output", type=Path, required=True, help="출력 디렉터리")
    args = parser.parse_args(argv)
    digest = _sha256(args.source)
    if digest != SOURCE.sha256:
        raise SystemExit(f"원본 SHA-256이 검증한 판본과 다릅니다: {digest}. 같은 정정판인지 확인하세요.")
    args.output.mkdir(parents=True, exist_ok=True)
    parts = {}
    with TemporaryDirectory() as directory, zipfile.ZipFile(args.source) as archive:
        for part in SOURCE.parts:
            path = Path(archive.extract(part.member, directory))
            name = output_name(part.id)
            parts[part.id] = {"file": name, **prepare_part(path, part.role, args.output / name)}
            path.unlink()
            print(f"{part.title}: {parts[part.id]['rows']}행 → {name}", flush=True)
    manifest = {
        "source": {"id": SOURCE.id, "url": SOURCE.url, "sha256": digest, "citations": list(SOURCE.citations),
                   "terms": SOURCE.terms, "terms_version": SOURCE.terms_version,
                   "recipe_version": SOURCE.recipe_version},
        "topic": SOURCE.topic,
        "features": list(NETWORK_FLOW_FEATURES),
        "observation_time": "ts (원본 Timestamp를 UTC로 해석)",
        "label": "label = 1 if 원본 Label != BENIGN. Attempted 행은 제외.",
        "parts": parts,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {args.output / 'manifest.json'}")


if __name__ == "__main__":
    main()
