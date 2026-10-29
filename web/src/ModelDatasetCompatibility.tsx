import type { DatasetColumns, ModelRevision } from "./types";

/** Worker가 요구하는 Feature 이름·수치형·순서별 자료형만 비교한다. 값·성능 검증은 아니다. */
export function checkModelDatasetInput(model: Pick<ModelRevision, "features" | "runtime">,
  columns: DatasetColumns["columns"]) {
  const types = model.runtime.feature_dtypes;
  const knownTypes = model.features.length > 0 && Array.isArray(types) && types.length === model.features.length &&
    types.every(dtype => typeof dtype === "string");
  const missing = model.features.filter(name => !columns.some(column => column.name === name));
  const mismatches = model.features.flatMap((name, index) => {
    const column = columns.find(item => item.name === name);
    if (!column) return [];
    const expected = knownTypes ? String(types[index]) : null;
    return !/^(?:U?Int(?:8|16|32|64)|Float(?:32|64))$/.test(column.dtype) ||
      (expected !== null && column.dtype !== expected)
      ? [{ name, expected, actual: column.dtype }] : [];
  });
  return { missing, mismatches, knownTypes,
    ready: knownTypes && missing.length === 0 && mismatches.length === 0 };
}

export function ModelDatasetCompatibility({ model, columns, compact = false }: {
  compact?: boolean;
  model: Pick<ModelRevision, "features" | "runtime">; columns: DatasetColumns["columns"];
}) {
  const input = checkModelDatasetInput(model, columns);
  return <div className={input.ready ? "scoring-input-ready" : "notice-blocked"} role="status">
    <strong>{input.ready ? "선택한 Dataset 입력 구조 일치"
      : input.missing.length > 0 || input.mismatches.length > 0
        ? "선택한 Dataset에 적용할 수 없음" : "선택한 Dataset 적용 조건 확인 필요"}</strong>
    {input.missing.length > 0 && <p>필수 Feature {input.missing.length}개 없음 · {input.missing.slice(0, 3).join(", ")}{input.missing.length > 3 ? " 외" : ""}</p>}
    {input.mismatches.length > 0 && <p>자료형 불일치 {input.mismatches.length}개 · {input.mismatches.slice(0, 3).map(item =>
      `${item.name}: 실제 ${item.actual} / 요구 ${item.expected ?? "수치형"}`).join(", ")}{input.mismatches.length > 3 ? " 외" : ""}</p>}
    {!input.knownTypes && <p>모델의 입력 자료형 기록이 없어 호환 여부를 확인할 수 없습니다.</p>}
    {!compact && <p className="hint">{input.ready
      ? "Feature 이름·자료형 확인 완료 · 값·결측 조건은 실행 시 검증합니다. 데이터의 업무적 의미나 모델 적합성을 보장하지 않습니다."
      : "이 모델의 입력 조건을 확인하거나 다른 Dataset을 선택하세요. 데이터는 자동 변환하지 않습니다."}</p>}
  </div>;
}
