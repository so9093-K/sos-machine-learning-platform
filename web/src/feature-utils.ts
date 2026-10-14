/** Pure UI validation; API validates all requests independently. */
export type FeatureStep = {
  op: string; column: string; value?: number | string;
  periods?: number; window?: number; dtype?: string;
};
export function createStep(op: string, column: string, raw: string): FeatureStep {
  if (!column) throw new Error("컬럼을 선택해 주세요.");
  if (op === "fill_null") {
    if (!raw.trim()) throw new Error("결측값 대체 값을 입력해 주세요.");
    const parsed = Number(raw);
    return { op, column, value: Number.isFinite(parsed) ? parsed : raw };
  }
  if (op === "cast") return { op, column, dtype: "Float64" };
  if (op === "lag") return { op, column, periods: 1 };
  if (op === "rolling_mean") return { op, column, window: 3 };
  if (op === "filter_not_null") return { op, column };
  throw new Error("지원하지 않는 변환입니다.");
}
export function needsTimeOrder(steps: FeatureStep[]): boolean {
  return steps.some(step => step.op === "lag" || step.op === "rolling_mean");
}
export function histogram(rows: Record<string, unknown>[], column: string, bins = 8):
  { labels: string[]; counts: number[] } | null {
  if (!column) return null;
  const values = rows.map(row => row[column]).filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value));
  if (!values.length) return null;
  const min = Math.min(...values), max = Math.max(...values);
  if (min === max) return { labels: [min.toFixed(2)], counts: [values.length] };
  const counts = Array.from({ length: bins }, () => 0);
  values.forEach(value => {
    const index = Math.min(bins - 1, Math.floor((value - min) / (max - min) * bins));
    counts[index]++;
  });
  return {
    labels: counts.map((_, i) => (min + (max - min) * i / bins).toFixed(2)),
    counts,
  };
}
