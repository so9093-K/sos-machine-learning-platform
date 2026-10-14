"""첫 사용자가 실제 분석을 수행할 수 있도록 결정적인 합성 데이터를 제공한다."""

import csv
import hashlib
import io

from .datasets import save_upload
from .store import Store


def _csv(headers: list[str], rows: list[list[object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _machine_sensors() -> bytes:
    rows = []
    for machine in ("A", "B"):
        for tick in range(1, 49):
            temperature = (
                ""
                if tick % 13 == 0
                else round(
                    20
                    + (tick % 9) * 0.7
                    + (5 if machine == "B" else 0)
                    + (14 if tick == 32 else 0),
                    2,
                )
            )
            vibration = round(0.4 + (tick % 7) * 0.06 + (1.8 if tick == 32 else 0), 3)
            rows.append([machine, tick, temperature, vibration, round(1.8 + tick / 120, 3)])
    return _csv(["machine_id", "time", "temperature", "vibration", "pressure"], rows)


def _data_quality() -> bytes:
    rows = []
    for i in range(1, 49):
        rows.append(
            [
                i,
                f"batch-{(i - 1) // 8 + 1}",
                "" if i % 11 == 0 else round(10 + i / 8, 2),
                "warning" if i % 9 == 0 else "ok",
                "A" if i % 2 == 0 else "B",
            ]
        )
    return _csv(["row_id", "batch", "measurement", "quality", "line"], rows)


# 샘플은 서버 시작 시 DB에 등록하지 않는다. 사용자가 선택할 때 같은 Dataset API를 사용한다.
_EXAMPLES = {
    "machine-sensors": {
        "name": "example-machine-sensors.csv",
        "title": "설비 센서 시계열",
        "description": "2개 설비의 시계열, 결측값, 일부 이상값을 탐색합니다.",
        "columns": ["machine_id", "time", "temperature", "vibration", "pressure"],
        "content": _machine_sensors(),
        "guide": "EDA에서 분포를 확인하고 Features에서 시간 컬럼과 설비별 그룹을 선택하세요.",
    },
    "data-quality": {
        "name": "example-data-quality.csv",
        "title": "데이터 품질 탐색",
        "description": "범주형 값과 결측값을 가진 합성 측정 데이터입니다.",
        "columns": ["row_id", "batch", "measurement", "quality", "line"],
        "content": _data_quality(),
        "guide": "결측 컬럼을 확인하고 값 대체 또는 결측 행 제외를 실험하세요.",
    },
}


def list_examples() -> list[dict]:
    """예제의 구조와 내용 버전을 읽기 전용으로 제공한다."""
    return [
        {
            "id": key,
            "title": value["title"],
            "description": value["description"],
            "columns": value["columns"],
            "guide": value["guide"],
            "row_count": value["content"].count(b"\n") - 1,
            "version": hashlib.sha256(value["content"]).hexdigest(),
        }
        for key, value in _EXAMPLES.items()
    ]


def use_example(store: Store, example_id: str) -> dict:
    """같은 예제를 반복 선택하더라도 별도의 불필요한 Dataset을 생성하지 않는다."""
    if example_id not in _EXAMPLES:
        raise KeyError(example_id)
    example = _EXAMPLES[example_id]
    digest = hashlib.sha256(example["content"]).hexdigest()
    for dataset in store.datasets():
        if dataset["name"] == example["name"] and dataset["version"] == digest:
            return dataset
    return save_upload(store, example["name"], example["content"])
