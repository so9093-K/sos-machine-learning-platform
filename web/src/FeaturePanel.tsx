import type { useFeatureEditor } from "./useFeatureEditor";
import { formatDtype } from "./display-format";

const opLabels: Record<string, string> = {
  select: "컬럼 선택", filter_not_null: "결측 행 제외", fill_null: "결측값 대체",
  cast: "실수형(Float64) 변환", lag: "이전 행 값 (Lag)", rolling_mean: "이동 평균 (Rolling Mean)",
};
const MAX_GROUP_COLUMNS = 4;

/** UI만 담당하며 Feature 초안·변환 계산 상태는 editor가 소유한다. */
export function FeaturePanel({
  editor, columnNames, busy, canRun, onSubmit,
}: {
  editor: ReturnType<typeof useFeatureEditor>;
  columnNames: string[];
  busy: boolean;
  canRun: boolean;
  onSubmit: () => void;
}) {
  const {
    steps, editingStep, lagPeriods, setLagPeriods, rollingWindow, setRollingWindow,
    op, setOp, column, setColumn, value, setValue, orderBy, setOrderBy,
    groupBy, setGroupBy, chosenColumns, setChosenColumns,
    featurePreview, addStep, editStep, moveStep, deleteStep, cancelEdit, previewFeatures,
  } = editor;
  return (
        <section className="panel" aria-label="데이터 준비">
          <div className="panel-head"><div><h3>데이터 변환</h3>
            <p>원본은 유지됩니다. 순서대로 변환 단계를 편집·검증하고 전체 Job으로 실행합니다.</p>
          </div></div>
          <div className="form-row">
            <label>변환 연산 <select aria-label="변환 연산" value={op} onChange={e => setOp(e.target.value)}>
              {Object.entries(opLabels).map(([key, label]) =>
                <option key={key} value={key}>{label}</option>)}
            </select></label>
            {op === "select" && <fieldset className="column-picks">
              <legend>유지할 컬럼</legend>
              {columnNames.map(name => <label key={name}>
                <input type="checkbox" checked={chosenColumns.includes(name)}
                  onChange={event => setChosenColumns(current => event.target.checked
                    ? [...current, name] : current.filter(item => item !== name))} />
                {name}
              </label>)}
            </fieldset>}
            {op !== "select" && <label>컬럼 <select aria-label="컬럼" value={column} onChange={e => setColumn(e.target.value)}>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>}
            {op === "fill_null" && <label>대체 값 <input value={value}
              onChange={e => setValue(e.target.value)} placeholder="0" /></label>}
            {op === "lag" && <label>이전 행 수 <input type="number" min={1} max={1000}
              value={lagPeriods} onChange={e => setLagPeriods(Number(e.target.value))} /></label>}
            {op === "rolling_mean" && <label>윈도 행 수 <input type="number" min={2} max={1000}
              value={rollingWindow} onChange={e => setRollingWindow(Number(e.target.value))} /></label>}
            <button className="secondary" onClick={addStep} disabled={
              (op === "select" ? chosenColumns.length === 0 : !column) ||
              (steps.length >= 30 && editingStep === null)
            }>
              {editingStep === null ? "+ 단계 추가" : "단계 수정 반영"}</button>
            {editingStep !== null && <button className="ghost" onClick={cancelEdit}>
              수정 취소</button>}
          </div>
          {(op === "lag" || op === "rolling_mean" || steps.some(step => step.op === "lag" || step.op === "rolling_mean")) &&
          <div className="window-transform-settings">
            <p className="hint">시간·그룹 기준은 이 변환의 모든 Lag·Rolling 단계에 공통으로 적용합니다.</p>
            <div className="form-row">
            <label>시간·정렬 컬럼 <select aria-label="시간·정렬 컬럼" value={orderBy} onChange={e => {
              const next = e.target.value;
              setOrderBy(next);
              // 정렬 컬럼은 그룹 키가 될 수 없다.
              setGroupBy(current => current.filter(item => item !== next));
            }}>
              <option value="">정렬하지 않음</option>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <fieldset className="column-picks">
              <legend>그룹 컬럼 (선택, 최대 {MAX_GROUP_COLUMNS}개)</legend>
              {columnNames.filter(c => c !== orderBy).map(name => <label key={name}>
                <input type="checkbox" checked={groupBy.includes(name)}
                  disabled={!groupBy.includes(name) && groupBy.length >= MAX_GROUP_COLUMNS}
                  onChange={event => setGroupBy(event.target.checked
                    ? [...groupBy, name].slice(0, MAX_GROUP_COLUMNS)
                    : groupBy.filter(item => item !== name))} />
                {name}
              </label>)}
            </fieldset>
            <p className="hint">Lag / Rolling은 시간 정렬 후 그룹별 계산합니다. Rolling Mean에는 현재 행이 포함됩니다.</p>
            </div>
          </div>}
          <div className="steps">{steps.length === 0 && <p className="muted">아직 변환 단계가 없습니다.</p>}
            {steps.map((step, i) => <div className="step" key={i}>
              <strong>{i + 1}. {opLabels[step.op] ?? step.op}</strong>
              <span>{step.op === "select" ? (step.columns ?? []).join(", ") : step.column}
                {step.op === "fill_null" ? ` · ${String(step.value)}(으)로 대체` : ""}
                {step.periods ? ` · ${step.periods}행 이전` : ""}
                {step.window ? ` · ${step.window}행 평균` : ""}</span>
              <button className="ghost" aria-label={`${i + 1}번째 단계 위로 이동`}
                disabled={i === 0} onClick={() => moveStep(i, -1)}>↑</button>
              <button className="ghost" aria-label={`${i + 1}번째 단계 아래로 이동`}
                disabled={i === steps.length - 1} onClick={() => moveStep(i, 1)}>↓</button>
              <button className="ghost" aria-label={`${i + 1}번째 단계 수정`}
                onClick={() => editStep(i)}>수정</button>
              <button className="ghost" aria-label={`${i + 1}번째 단계 삭제`}
                onClick={() => deleteStep(i)}>삭제</button>
            </div>)}</div>
          <div className="actions"><button disabled={busy || !steps.length} className="secondary"
            onClick={() => void previewFeatures()}>변환 결과 미리보기</button>
            {canRun && <button disabled={busy || !steps.length} onClick={onSubmit}>
              전체 변환 실행 →
            </button>}</div>
          {featurePreview && <div className="result"><h4>변환 미리보기 · 전체 입력 기준 연산</h4>
             <p className="hint">입력을 표본 추출하는 것이 아니라 실제 변환 순서를 적용한 결과를 최대 30행 반환합니다. 단순 변환은 필요한 행만 읽고, 전역 Window는 동기 예산 초과 시 전체 Job을 안내합니다.</p>
            <div className="table-scroll"><table><thead><tr>{Object.entries(featurePreview.schema).map(
              ([c, t]) => <th key={c}>{c}<small>{formatDtype(t)}</small></th>
            )}</tr></thead><tbody>{featurePreview.rows.map((r, i) => <tr key={i}>
              {Object.keys(featurePreview.schema).map(c => <td key={c}>{r[c] == null ? "—" : String(r[c])}</td>)}
            </tr>)}</tbody></table></div></div>}
        </section>
  );
}
