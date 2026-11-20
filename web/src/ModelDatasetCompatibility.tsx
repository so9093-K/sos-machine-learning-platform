import { formatDtype } from "./display-format";
import type { DatasetColumns, ModelRevision } from "./types";

type Columns = DatasetColumns["columns"];
type TypeMismatch = { name: string; expected: string; actual: string };

/** 데이터 준비의 자료형 변환이 만드는 대상 자료형. 다른 자료형은 원본에서 맞춰야 한다. */
export const PREPARATION_CAST_DTYPE = "Float64";
// 실수형 변환이 값의 의미를 바꾸지 않는 원본 자료형. 문자열은 숫자로 읽히지 않으면 변환 Job이 실패한다.
const CASTABLE_SOURCE = /^(?:U?Int(?:8|16|32|64|128)|Float32|Decimal.*|String)$/;
const LIST_PREVIEW = 6;

/** 대소문자·앞뒤 공백·공백/하이픈 구분자만 무시한 이름. 컬럼의 의미가 같은지는 판단하지 않는다. */
export function normalizedFeatureName(name: string): string {
  return name.trim().toLowerCase().replace(/[\s-]+/g, "_");
}

/** Worker가 요구하는 Feature 이름·수치형·순서별 자료형만 비교한다. 값·성능 검증은 아니다.
 *
 *  불일치는 사용자가 할 수 있는 다음 행동별로 나눈다.
 *  - `castable`: 데이터 준비의 실수형 변환으로 맞출 수 있는 자료형 불일치
 *  - `renamed`: 정확한 이름은 없지만 정규화 이름이 같은 컬럼이 하나뿐인 Feature. 자동으로 바꾸지 않는다.
 */
export function checkModelDatasetInput(model: Pick<ModelRevision, "features" | "runtime">,
  columns: Columns) {
  // 판본은 학습·연결 때 Feature 순서별 수치 자료형을 항상 기록한다.
  const types = model.runtime.feature_dtypes as string[];
  const missing = model.features.filter(name => !columns.some(column => column.name === name));
  const mismatches: TypeMismatch[] = model.features.flatMap((name, index) => {
    const column = columns.find(item => item.name === name);
    const expected = String(types[index]);
    return column && column.dtype !== expected ? [{ name, expected, actual: column.dtype }] : [];
  });
  const renamed = missing.flatMap(name => {
    const candidates = columns.filter(column => !model.features.includes(column.name) &&
      normalizedFeatureName(column.name) === normalizedFeatureName(name));
    return candidates.length === 1 ? [{ name, actual: candidates[0].name }] : [];
  });
  const castable = mismatches.filter(item =>
    item.expected === PREPARATION_CAST_DTYPE && CASTABLE_SOURCE.test(item.actual));
  return { missing, mismatches, renamed, castable,
    ready: missing.length === 0 && mismatches.length === 0 };
}

/** 앞뒤 공백은 화면에서 보이지 않으므로 따옴표로 감싸 드러낸다. */
function visibleName(name: string): string {
  return name === name.trim() ? name : JSON.stringify(name);
}

function NameList({ items }: { items: string[] }) {
  const shown = items.slice(0, LIST_PREVIEW);
  return <>
    <ul className="input-fix-list">{shown.map(item => <li key={item}><code>{item}</code></li>)}</ul>
    {items.length > LIST_PREVIEW && <details className="input-fix-more">
      <summary>전체 {items.length}개</summary>
      <ul className="input-fix-list">{items.slice(LIST_PREVIEW).map(item =>
        <li key={item}><code>{item}</code></li>)}</ul>
    </details>}
  </>;
}

export function ModelDatasetCompatibility({ model, columns, compact = false, onPrepare }: {
  compact?: boolean;
  model: Pick<ModelRevision, "features" | "runtime">; columns: Columns;
  /** 자료형 변환으로 맞출 수 있는 불일치가 있을 때 데이터 준비로 이동한다. */
  onPrepare?: () => void;
}) {
  const input = checkModelDatasetInput(model, columns);
  if (input.ready) return <div className="scoring-input-ready" role="status">
    <strong>선택한 Dataset 입력 구조 일치</strong>
    {!compact && <p className="hint">이름·자료형 일치 · 값과 결측은 실행할 때 확인하며, 업무 적합성을 뜻하지는 않습니다.</p>}
  </div>;
  if (compact) return <div className="notice-blocked" role="status">
    <strong>선택한 Dataset에 적용할 수 없음</strong>
    {input.missing.length > 0 && <p>필수 Feature {input.missing.length}개 없음 · {input.missing.slice(0, 3).join(", ")}{input.missing.length > 3 ? " 외" : ""}</p>}
    {input.mismatches.length > 0 && <p>자료형 불일치 {input.mismatches.length}개 · {input.mismatches.slice(0, 3).map(item =>
      `${item.name}: 실제 ${item.actual} / 요구 ${item.expected}`).join(", ")}{input.mismatches.length > 3 ? " 외" : ""}</p>}
  </div>;
  const renamedNames = new Set(input.renamed.map(item => item.name));
  const absent = input.missing.filter(name => !renamedNames.has(name));
  const castableNames = new Set(input.castable.map(item => item.name));
  const sourceTypes = input.mismatches.filter(item => !castableNames.has(item.name));
  const matched = model.features.length - input.missing.length - input.mismatches.length;
  const remaining = absent.length + input.renamed.length + sourceTypes.length;
  return <div className="notice-blocked model-input-check" role="status">
    <strong>선택한 Dataset에 적용할 수 없음</strong>
    <p>필수 Feature {model.features.length}개 중 이름·자료형 일치 {matched}개</p>
    {input.castable.length > 0 && <section className="input-fix" aria-label="자료형 변환으로 해결">
      <strong>자료형 변환으로 해결 · {input.castable.length}개</strong>
      <p className="hint">데이터 준비에서 실수형({PREPARATION_CAST_DTYPE})으로 변환하면 맞출 수 있습니다.
        원본은 그대로 두고 변환 결과를 새 Dataset으로 만들며, 숫자로 읽을 수 없는 값이 있으면 변환이 실패합니다.</p>
      <NameList items={input.castable.map(item => `${item.name}: ${formatDtype(item.actual)} → ${formatDtype(item.expected)}`)} />
      {onPrepare && <button type="button" className="secondary" onClick={onPrepare}>데이터 준비에서 자료형 변환 →</button>}
      {onPrepare && remaining > 0 && <p className="hint">자료형 변환 후에도 아래 {remaining}개는 원본에서 준비해야 합니다.</p>}
    </section>}
    {input.renamed.length > 0 && <section className="input-fix" aria-label="이름만 다른 컬럼">
      <strong>이름만 다름 · {input.renamed.length}개</strong>
      <p className="hint">대소문자·공백·구분자만 다른 컬럼입니다. 같은 값인지 확인한 뒤 원본에서 컬럼 이름을 바꿔 다시 등록하세요.</p>
      <NameList items={input.renamed.map(item => `${visibleName(item.actual)} → ${item.name}`)} />
    </section>}
    {absent.length > 0 && <section className="input-fix" aria-label="없는 Feature">
      <strong>없는 Feature · {absent.length}개</strong>
      <p className="hint">데이터 준비로는 새 Feature를 만들 수 없습니다. 원본 데이터를 만드는 단계에서 같은 이름의 컬럼으로 준비하세요.</p>
      <NameList items={absent} />
    </section>}
    {sourceTypes.length > 0 && <section className="input-fix" aria-label="원본 자료형 변경 필요">
      <strong>원본 자료형 변경 필요 · {sourceTypes.length}개</strong>
      <p className="hint">데이터 준비에서 만들 수 없는 자료형입니다. 원본에서 요구 자료형으로 저장해 다시 등록하세요.</p>
      <NameList items={sourceTypes.map(item => `${item.name}: ${formatDtype(item.actual)} → ${formatDtype(item.expected)}`)} />
    </section>}
  </div>;
}
