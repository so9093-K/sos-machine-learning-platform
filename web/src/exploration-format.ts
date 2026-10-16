/** 샘플·전체 Job 화면이 같은 값·그룹 의미를 표시한다. */
export function explorationCategory(value: unknown, groupBy: string | null | undefined): string {
  if (value == null) return groupBy ? "(NULL 그룹)" : "전체";
  return typeof value === "string" ? value : String(value);
}

/** 정밀 DECIMAL·큰 정수 문자열은 Number()로 변환하지 않는다. */
export function explorationValue(value: number | string | null | undefined): string {
  if (value == null) return "NULL";
  return typeof value === "number" ? value.toLocaleString("ko-KR") : value;
}

/** 문자열 숫자는 차트에서 반올림될 위험이 있으므로 표에만 표시한다. */
export function chartValue(value: number | string | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
