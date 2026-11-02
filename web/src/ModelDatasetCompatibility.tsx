import type { DatasetColumns, ModelRevision } from "./types";

/** Worker가 요구하는 Feature 이름·수치형·순서별 자료형만 비교한다. 값·성능 검증은 아니다. */
export function checkModelDatasetInput(model: Pick<ModelRevision, "features" | "runtime">,
  columns: DatasetColumns["columns"]) {
  // 판본은 학습·연결 때 Feature 순서별 수치 자료형을 항상 기록한다.
  const types = model.runtime.feature_dtypes as string[];
  const missing = model.features.filter(name => !columns.some(column => column.name === name));
  const mismatches = model.features.flatMap((name, index) => {
    const column = columns.find(item => item.name === name);
    const expected = String(types[index]);
    return column && column.dtype !== expected ? [{ name, expected, actual: column.dtype }] : [];
  });
  return { missing, mismatches, ready: missing.length === 0 && mismatches.length === 0 };
}

export function ModelDatasetCompatibility({ model, columns, compact = false }: {
  compact?: boolean;
  model: Pick<ModelRevision, "features" | "runtime">; columns: DatasetColumns["columns"];
}) {
  const input = checkModelDatasetInput(model, columns);
  return <div className={input.ready ? "scoring-input-ready" : "notice-blocked"} role="status">
    <strong>{input.ready ? "선택한 Dataset 입력 구조 일치" : "선택한 Dataset에 적용할 수 없음"}</strong>
    {input.missing.length > 0 && <p>필수 Feature {input.missing.length}개 없음 · {input.missing.slice(0, 3).join(", ")}{input.missing.length > 3 ? " 외" : ""}</p>}
    {input.mismatches.length > 0 && <p>자료형 불일치 {input.mismatches.length}개 · {input.mismatches.slice(0, 3).map(item =>
      `${item.name}: 실제 ${item.actual} / 요구 ${item.expected}`).join(", ")}{input.mismatches.length > 3 ? " 외" : ""}</p>}
    {!compact && <p className="hint">{input.ready
      ? "이름·자료형 일치 · 값과 결측은 실행할 때 확인하며, 업무 적합성을 뜻하지는 않습니다."
      : "이 모델의 입력 조건을 확인하거나 다른 Dataset을 선택하세요. 데이터는 자동 변환하지 않습니다."}</p>}
  </div>;
}
