import { useMemo } from "react";
import { DataChart } from "./DataChart";
import type { ModelRevision } from "./types";

/** 보존된 전체 집계만 분포로 사용한다. 구간 집계로 정확한 백분위수를 만들지 않는다. */
export function verifiedScoreHistogram(summary: Record<string, unknown>, rowCount: number):
  { edges: number[]; counts: number[] } | null {
  const raw = summary.histogram;
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  const edges = data.bin_edges;
  const counts = data.bin_counts;
  if ((!Number.isInteger(rowCount) || rowCount <= 0) || data.method !== "equal_width_exact_v1" || !Array.isArray(edges) || !Array.isArray(counts) ||
      counts.length === 0 || counts.length > 128 || edges.length !== counts.length + 1 ||
      data.score_count !== rowCount ||
      !edges.every((value, index) => typeof value === "number" && Number.isFinite(value) &&
        (index === 0 || value > edges[index - 1])) ||
      !counts.every(value => typeof value === "number" && Number.isInteger(value) && value >= 0) ||
      counts.reduce((sum, value) => sum + value, 0) !== rowCount) return null;
  return { edges: edges as number[], counts: counts as number[] };
}

export function ModelScoreContext({ model, score, comparisonScore }: {
  model: ModelRevision; score: number; comparisonScore?: number | null;
}) {
  const summary = model.training_score_summary;
  const count = typeof summary.score_count === "number" && Number.isSafeInteger(summary.score_count) &&
    summary.score_count > 0 ? summary.score_count : 0;
  const histogram = verifiedScoreHistogram(summary, count);
  const low = summary.score_min;
  const high = summary.score_max;
  const rangeReady = typeof low === "number" && Number.isFinite(low) &&
    typeof high === "number" && Number.isFinite(high) && low <= high;
  const position = !rangeReady || count <= 0 || !Number.isFinite(score) ? null : score > high ? "학습 입력에서 나온 최대 점수보다 높습니다."
    : score < low ? "학습 입력에서 나온 최소 점수보다 낮습니다."
    : "학습 입력에서 관측된 점수 범위 안에 있습니다.";
  const option = useMemo(() => {
    if (!histogram || !Number.isFinite(score)) return null;
    const { edges, counts } = histogram;
    const comparison = typeof comparisonScore === "number" && Number.isFinite(comparisonScore)
      ? comparisonScore : score;
    return {
      tooltip: { trigger: "item" },
      grid: { left: 48, right: 25, top: 24, bottom: 44 },
      xAxis: { type: "value" as const, name: "이상 점수", nameLocation: "middle" as const, nameGap: 28,
        min: Math.min(edges[0], score, comparison), max: Math.max(edges.at(-1)!, score, comparison),
        axisLabel: { hideOverlap: true, formatter: (value: number) => value.toPrecision(3) } },
      yAxis: { type: "value" as const, name: "학습 입력 수", minInterval: 1 },
      series: [{ type: "bar" as const, name: "학습 점수 분포",
        data: counts.map((value, index) => [(edges[index] + edges[index + 1]) / 2, value]),
        markLine: { silent: true, symbol: "none", label: { show: false }, data: [
          { xAxis: score }, ...(comparison !== score ? [{ xAxis: comparison, lineStyle: { type: "dotted" as const } }] : []),
        ] } }],
    };
  }, [summary, score, comparisonScore]);
  return <section className="model-score-context" aria-label="학습 점수와 현재 입력 비교">
    <h5>학습 점수 분포 비교</h5>
    {position && <p>{position}</p>}
    {option ? <>
      <DataChart option={option} ariaLabel="이 모델의 실제 학습 점수 분포와 계산한 입력의 점수 위치" />
      <p className="hint">학습에 사용한 {count.toLocaleString("ko-KR")}건과 비교 · 점선은 이번 점수 {score.toFixed(6)}
        {comparisonScore != null && comparisonScore !== score ? ` · 촘촘한 점선은 비교 점수 ${comparisonScore.toFixed(6)}` : ""}</p>
    </> : <p className="hint">이 모델에는 비교할 학습 점수 분포가 보존돼 있지 않습니다.</p>}
    <p className="hint">학습 데이터에도 이례적인 활동이 포함될 수 있습니다. 이 위치는 정상 여부나 사건 발생 확률을 뜻하지 않습니다.</p>
  </section>;
}
