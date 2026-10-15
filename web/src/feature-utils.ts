/** API 변환 계약은 Pydantic에서 생성된 타입을 그대로 사용한다. */
import type { components } from "./api.generated";

type ApiStep = components["schemas"]["Step"];
// Pydantic가 기본값을 채우는 필드는 UI 요청 초안에서 생략할 수 있다.
export type FeatureStep = Omit<ApiStep, "periods" | "window"> &
  Partial<Pick<ApiStep, "periods" | "window">>;
export function createStep(
  op: string, column: string, raw: string, dtype?: string, size?: number,
): FeatureStep {
  if (!column) throw new Error("컬럼을 선택해 주세요.");
  if (op === "fill_null") {
    if (!raw.trim()) throw new Error("결측값 대체 값을 입력해 주세요.");
    if (dtype === "String" || dtype === "Categorical") {
      return { op, column, value: raw };
    }
    const parsed = Number(raw);
    return { op, column, value: Number.isFinite(parsed) ? parsed : raw };
  }
  if (op === "cast") return { op, column, dtype: "Float64" };
  if (op === "lag" || op === "rolling_mean") {
    const parameter = size ?? (op === "lag" ? 1 : 3);
    const minimum = op === "lag" ? 1 : 2;
    if (!Number.isInteger(parameter) || parameter < minimum || parameter > 1000) {
      throw new Error(`크기는 ${minimum}~1000 범위의 정수여야 합니다.`);
    }
    return op === "lag" ? { op, column, periods: parameter }
      : { op, column, window: parameter };
  }
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
