/** API 식별자·엔진 표기를 사용자 화면 표기와 분리한다. 원본 값은 title 등 기술 근거로 남긴다. */

const jobStatusLabels: Record<string, string> = {
  Queued: "대기 중", Running: "진행 중", Succeeded: "완료", Failed: "실패", Cancelled: "취소됨",
  Interrupted: "중단됨",
};

export function jobStatusLabel(status: string): string {
  return jobStatusLabels[status] ?? status;
}

/** Polars 자료형의 Python 표현만 간결하게 바꾸며 자료형 이름 자체는 유지한다. */
export function formatDtype(dtype: string): string {
  const datetime = /^Datetime\(time_unit='\w+', time_zone=(?:'([^']+)'|None)\)$/.exec(dtype);
  if (datetime) return datetime[1] ? `Datetime (${datetime[1]})` : "Datetime";
  const decimal = /^Decimal\(precision=(\d+|None), scale=(\d+)\)$/.exec(dtype);
  if (decimal) return decimal[1] === "None" ? `Decimal (scale ${decimal[2]})`
    : `Decimal(${decimal[1]}, ${decimal[2]})`;
  if (/^Duration\(time_unit='\w+'\)$/.test(dtype)) return "Duration";
  return dtype;
}

/** 관측 시각을 UTC 기준의 읽기 쉬운 형태로 보여준다. 해석할 수 없는 값은 그대로 둔다. */
export function formatObservedAt(value: string): string {
  const time = Date.parse(value);
  if (Number.isNaN(time)) return value;
  return new Date(time).toISOString().replace("T", " ").replace(/(\.000)?Z$/, " UTC");
}

const roleLabels: Record<string, string> = {
  temporal: "시간", identifier: "식별자", categorical: "범주", numeric_measure: "수치",
  potentially_sensitive: "민감 정보 가능",
};
const basisLabels: Record<string, string> = {
  column_name_hint: "컬럼명", physical_dtype: "자료형", sample_cardinality: "고유값 수",
};

/** 역할 후보와 그 근거를 미확정 후보로 읽히도록 표기한다. */
export function roleCandidateLabel(role: string, basis: string): string {
  return `${roleLabels[role] ?? role} (${basisLabels[basis] ?? basis} 기준)`;
}
