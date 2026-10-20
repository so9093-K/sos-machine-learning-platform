"""결정적 합성 데이터로 SOS Polars Transform의 시간·메모리 기준선을 수집한다.

실제 처리량이나 cgroup/OS 격리를 보증하는 벤치마크가 아니다.
한 프로세스에서 하나의 시나리오만 실행해 결과를 JSON으로 출력한다.
"""

import argparse
import json
import time
from pathlib import Path

import polars as pl

from sos_platform.processing import transform
from sos_platform.schemas import Transform


def linux_memory_bytes(field: str) -> int | None:
    """Linux /proc의 현재 RSS 또는 프로세스 생애 고수위 값을 읽는다."""
    source = Path("/proc/self/status")
    if not source.is_file():
        return None
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{field}:"):
            columns = line.split()
            if len(columns) == 3 and columns[2] == "kB":
                return int(columns[1]) * 1024
    return None


def run_trial(rows: int, groups: int, scenario: str) -> dict:
    """동일한 데이터 발생 규칙에서 실제 SOS Transform 실행 결과와 비용을 관측한다.

    Args:
        rows: 생성할 합성 입력 행 수.
        groups: 구분할 머신 그룹 수. 모든 시간 키는 유일하다.
        scenario: row_local 또는 window.

    Returns:
        행 수·출력 크기·벽시계 시간과 Linux RSS 관측치를 담은 JSON 가능 사전.

    Raises:
        ValueError: 지원하지 않는 시나리오나 데이터 크기.
    """
    if not 1 <= rows <= 5_000_000 or not 1 <= groups <= rows:
        raise ValueError("rows must be 1..5000000, groups must be 1..rows")
    if scenario not in ("row_local", "window"):
        raise ValueError("scenario must be row_local or window")
    # 입력 프레임도 시간과 메모리를 사용하므로 측정 시작 전에 먼저 준비한다.
    source = (
        pl.DataFrame({"tick": pl.int_range(0, rows, eager=True)})
        .lazy()
        .reverse()
        .with_columns(
            (pl.col("tick") % groups).alias("machine"),
            (pl.col("tick") * 0.25).alias("value"),
        )
    )
    if scenario == "window":
        spec = Transform.model_validate(
            {
                "group_by": ["machine"],
                "order_by": "tick",
                "steps": [
                    {"op": "lag", "column": "value", "alias": "previous"},
                    {"op": "rolling_mean", "column": "value", "alias": "moving", "window": 3},
                ],
            }
        )
    else:
        spec = Transform.model_validate(
            {
                "steps": [
                    {"op": "select", "columns": ["tick", "machine", "value"]},
                    {"op": "filter_not_null", "column": "value"},
                ]
            }
        )
    rss_before = linux_memory_bytes("VmRSS")
    start = time.perf_counter()
    result = transform(source, spec).collect()
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    return {
        "scenario": scenario,
        "input_rows": rows,
        "groups": groups,
        "output_rows": result.height,
        "output_bytes_estimated": result.estimated_size(),
        "elapsed_ms": elapsed_ms,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": linux_memory_bytes("VmRSS"),
        "process_highwater_rss_bytes": linux_memory_bytes("VmHWM"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SOS Transform deterministic resource baseline")
    parser.add_argument("--rows", type=int, default=20_000)
    parser.add_argument("--groups", type=int, default=32)
    parser.add_argument("--scenario", choices=["row_local", "window"], default="window")
    options = parser.parse_args()
    print(json.dumps(run_trial(options.rows, options.groups, options.scenario), sort_keys=True))


if __name__ == "__main__":
    main()
