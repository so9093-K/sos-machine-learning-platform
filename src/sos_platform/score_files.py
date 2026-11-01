"""Parquet 점수 파일에 대한 크기와 무관한 정확한 계산.

Scoring 결과·학습 점수·생산 Pipeline의 평가 점수는 모두 Parquet의 `anomaly_score`(선택적으로 `label`)
열로 남긴다. 분포 요약과 임계값 판정은 배치를 한 번 순회하며 계산하고, 순서가 필요한 분위수 임계값과
순위 지표(Average Precision·ROC-AUC)는 DuckDB 외부 정렬·집계 결과를 순회한다. 어느 계산도 전체 점수를
메모리에 모으지 않으며, 결과의 의미는 메모리 내 함수(`ml_anomaly`)와 같다. 메모리·Stage로 수용할 수
없을 때만 `SpillBudget`의 오류로 보고한다.
"""

import math
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .duckdb_spill import SpillBudget, literal, order_statistics, quoted, spill_connection
from .ml_anomaly import (
    anomaly_score_summary,
    checked_threshold,
    histogram_edges,
    interpolate_threshold,
    quantile_position,
)

SCORE = "anomaly_score"
LABEL = "label"
_BATCH_ROWS = 65_536


class ScoreWriter:
    """점수(와 레이블)를 배치마다 Parquet에 기록한다. 기록한 행 수를 센다."""

    def __init__(self, path: Path, *, labeled: bool = False):
        fields = [pa.field(SCORE, pa.float64(), nullable=False)]
        if labeled:
            fields.append(pa.field(LABEL, pa.int8(), nullable=False))
        self._schema = pa.schema(fields)
        self._writer = pq.ParquetWriter(path, self._schema)
        self.count = 0

    def write(self, scores: Sequence[float], labels: Sequence[int] | None = None) -> None:
        columns = [pa.array(scores, type=pa.float64())]
        if labels is not None:
            columns.append(pa.array(labels, type=pa.int8()))
        self._writer.write_table(pa.Table.from_arrays(columns, schema=self._schema))
        self.count += len(scores)

    def __enter__(self) -> "ScoreWriter":
        return self

    def __exit__(self, *_exc) -> None:
        self._writer.close()


def _batches(path: Path, columns: list[str]) -> Iterator[pa.RecordBatch]:
    with pq.ParquetFile(path) as reader:
        yield from reader.iter_batches(batch_size=_BATCH_ROWS, columns=columns)


def _finite(values: np.ndarray) -> np.ndarray:
    if not np.isfinite(values).all():
        raise ValueError("Anomaly scores must be finite")
    return values


def summarize_scores(path: Path, *, threshold: float | None = None, labeled: bool = False) -> dict:
    """`evaluate_anomaly_scores`와 같은 요약을 한 번의 배치 순회로 만든다.

    평균은 모든 점수의 정확 합(fsum)이며, 레이블은 0/1만 허용한다.

    Raises:
        ValueError: 점수가 없거나 유한하지 않거나, 레이블이 0/1이 아니거나, 임계값 없이 레이블 평가를 요청했다.
    """
    threshold = checked_threshold(threshold)
    if labeled and threshold is None:
        raise ValueError("Labeled decision evaluation requires a threshold")
    state = {"count": 0, "low": math.inf, "high": -math.inf, "predicted": 0,
             "tp": 0, "fp": 0, "fn": 0}

    def values() -> Iterator[float]:
        for batch in _batches(path, [SCORE, LABEL] if labeled else [SCORE]):
            scores = _finite(batch.column(0).to_numpy(zero_copy_only=False).astype(np.float64))
            if not len(scores):
                continue
            state["count"] += len(scores)
            state["low"] = min(state["low"], float(scores.min()))
            state["high"] = max(state["high"], float(scores.max()))
            if threshold is not None:
                alerts = scores >= threshold
                state["predicted"] += int(alerts.sum())
            if labeled:
                labels = batch.column(1).to_numpy(zero_copy_only=False)
                if not np.isin(labels, (0, 1)).all():
                    raise ValueError("Labels must be binary 0/1")
                positive = labels == 1
                state["tp"] += int((alerts & positive).sum())
                state["fp"] += int((alerts & ~positive).sum())
                state["fn"] += int((~alerts & positive).sum())
            yield from scores.tolist()

    total = math.fsum(values())
    count = state["count"]
    if not count:
        raise ValueError("Anomaly scores must not be empty")
    confusion = None
    if labeled:
        tp, fp, fn = state["tp"], state["fp"], state["fn"]
        confusion = (tp, fp, count - tp - fp - fn, fn)
    return anomaly_score_summary(
        count=count, score_min=state["low"], score_max=state["high"], score_mean=total / count,
        threshold=threshold, predicted=state["predicted"] if threshold is not None else None,
        confusion=confusion,
    )


def label_counts(path: Path) -> tuple[int, int]:
    """(양성 수, 음성 수). 레이블은 0/1만 허용한다."""
    positives = negatives = 0
    for batch in _batches(path, [LABEL]):
        labels = batch.column(0).to_numpy(zero_copy_only=False)
        if not np.isin(labels, (0, 1)).all():
            raise ValueError("Labels must be binary 0/1")
        ones = int((labels == 1).sum())
        positives += ones
        negatives += len(labels) - ones
    return positives, negatives


def score_histogram_file(
    path: Path, *, low: float, high: float, expected_rows: int, bins: int = 32,
) -> dict:
    """`score_histogram`과 같은 고정 폭 구간 빈도. 구간은 이미 계산한 최솟값·최댓값으로 정한다."""
    edges = histogram_edges(low, high, bins=bins)
    counts = np.zeros(bins, dtype=np.int64)
    inspected = 0
    for batch in _batches(path, [SCORE]):
        values = _finite(batch.column(0).to_numpy(zero_copy_only=False))
        counts += np.histogram(values, bins=edges)[0]
        inspected += len(values)
    if inspected != expected_rows or int(counts.sum()) != expected_rows:
        raise ValueError("Histogram score count differs from the score file")
    return {
        "method": "equal_width_exact_v1",
        "bin_edges": edges,
        "bin_counts": counts.tolist(),
        "score_count": inspected,
        "constant_scores": low == high,
    }


def _readable(path: Path, budget: SpillBudget) -> list[Path]:
    return [] if path.resolve().is_relative_to(budget.stage.resolve()) else [path]


def quantile_threshold_file(path: Path, alert_fraction: float, budget: SpillBudget) -> float:
    """`quantile_threshold`와 같은 보간 임계값. 외부 정렬한 점수에서 필요한 두 순위만 읽는다."""
    with pq.ParquetFile(path) as reader:
        count = reader.metadata.num_rows
    if not count:
        raise ValueError("Anomaly scores must not be empty")
    lower, upper, position = quantile_position(count, alert_fraction)
    low_value, high_value = order_statistics(path, SCORE, [lower, upper], budget)
    if not all(isinstance(v, float) and math.isfinite(v) for v in (low_value, high_value)):
        raise ValueError("Anomaly scores must be finite")
    return interpolate_threshold(low_value, high_value, lower, position)


def ranking_metrics_file(
    path: Path, budget: SpillBudget, *, positives: int, negatives: int,
) -> tuple[float | None, float | None]:
    """Average Precision과 ROC-AUC를 scikit-learn과 같은 정의로 계산한다.

    점수가 같은 관측은 한 임계값으로 묶고(높은 점수부터), AP는 Σ(재현율 증가 × 정밀도),
    ROC-AUC는 ROC 곡선의 사다리꼴 면적이다. 고유 점수 집계와 정렬은 DuckDB가 Stage에 Spill하며
    수행하고, 결과를 배치로 순회한다. 양성 또는 음성이 없으면 둘 다 정의하지 않는다.
    """
    if not positives or not negatives:
        return None, None
    tp_before = fp_before = 0
    ap_parts: list[float] = []
    auc_parts: list[float] = []
    with spill_connection(budget, readable=_readable(path, budget)) as conn:
        reader = conn.execute(
            f"SELECT sum({quoted(LABEL)})::BIGINT AS pos, count(*)::BIGINT AS n "
            f"FROM read_parquet({literal(str(path))}) GROUP BY {quoted(SCORE)} "
            f"ORDER BY {quoted(SCORE)} DESC"
        ).to_arrow_reader(batch_size=_BATCH_ROWS)
        try:
            for batch in reader:
                pos = batch.column(0).to_numpy(zero_copy_only=False).astype(np.int64)
                neg = batch.column(1).to_numpy(zero_copy_only=False).astype(np.int64) - pos
                tp = tp_before + np.cumsum(pos)
                fp = fp_before + np.cumsum(neg)
                tp_prev, fp_prev = tp - pos, fp - neg
                recall, recall_prev = tp / positives, tp_prev / positives
                ap_parts.append(math.fsum(((recall - recall_prev) * (tp / (tp + fp))).tolist()))
                fpr, fpr_prev = fp / negatives, fp_prev / negatives
                auc_parts.append(math.fsum(((fpr - fpr_prev) * (recall + recall_prev) / 2).tolist()))
                tp_before, fp_before = int(tp[-1]), int(fp[-1])
        finally:
            reader.close()
    if (tp_before, fp_before) != (positives, negatives):
        raise ValueError("Ranking metric counts differ from the score file")
    return math.fsum(ap_parts), math.fsum(auc_parts)
