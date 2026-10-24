"""첫 사용자가 실제 분석을 수행할 수 있도록 결정적인 합성 데이터를 제공한다."""

import csv
import hashlib
import io
from uuid import UUID

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


def _activity_anomaly_if() -> bytes:
    """첫 등록형 IF Pipeline의 실제 입력 계약을 충족하는 재현 가능한 합성 데이터.

    원본 보안 이벤트나 학습 품질을 보증하는 자료가 아니다. 서로 다른 시간대와
    인위적인 값 변화를 포함하여 학습·보정·Test의 실행 경로를 경험하게 한다.
    """
    from datetime import datetime, timedelta, timezone

    from .pipeline_reference import FEATURES

    rows: list[list[object]] = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for tick in range(240):
        observed = start + timedelta(minutes=tick)
        outlier = tick in (204, 216, 228)
        values: list[object] = []
        for index, feature in enumerate(FEATURES):
            if feature.startswith("ratio_"):
                value = round(min(0.99, 0.07 + ((tick * 3 + index * 7) % 42) / 100
                                  + (0.42 if outlier else 0.0)), 5)
            elif feature.startswith(("cnt_", "uniq_")):
                value = (tick * (index % 4 + 1) + index * 3) % 17 + 1
                if outlier:
                    value += 90 + index
            else:
                value = round(1.0 + (index + 1) * 0.4
                              + ((tick * 7 + index * 11) % 37) / 12
                              + (110.0 + index if outlier else 0.0), 5)
            values.append(value)
        # 예제에서만 고정된 비식별 UUIDv4를 생성한다. 일반 사용자 데이터를 추정하지 않는다.
        digest = hashlib.sha256(f"sos-if-example-row:{tick}".encode()).digest()
        identity = str(UUID(bytes=digest[:16], version=4))
        rows.append([observed.isoformat(), identity, *values])
    return _csv(["ts", "sos_observation_id", *FEATURES], rows)


# 샘플은 서버 시작 시 DB에 등록하지 않는다. 사용자가 선택할 때 같은 Dataset API를 사용한다.
_EXAMPLES = {
    "activity-anomaly-if": {
        "name": "example-activity-anomaly-if.csv",
        "title": "활동 이상 탐지 학습 예제",
        "description": "44개 활동 Feature·UTC 시각·재현 가능한 비식별 관측치 ID를 포함한 합성 예제입니다.",
        "columns": ["ts", "sos_observation_id", "io_bytes_per_sec", "net_bytes_per_sec", "cnt_file_create", "cnt_proc_exec"],
        "content": _activity_anomaly_if(),
        "guide": "모델 실험실에서 모델을 학습하고 점수와 이상 후보를 살펴보세요. 실제 침해 사건이나 탐지 정확도를 검증하는 데이터가 아닙니다.",
    },
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


def use_example(store: Store, example_id: str, *, owner_actor: str | None = None) -> dict:
    """같은 예제를 반복 선택하더라도 별도의 불필요한 Dataset을 생성하지 않는다."""
    if example_id not in _EXAMPLES:
        raise KeyError(example_id)
    example = _EXAMPLES[example_id]
    digest = hashlib.sha256(example["content"]).hexdigest()
    existing = store.find_dataset_by_name_version(
        name=example["name"], version=digest, actor_id=owner_actor
    )
    if existing is not None:
        return existing
    return save_upload(store, example["name"], example["content"], owner_actor=owner_actor)
