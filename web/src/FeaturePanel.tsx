import type { useFeatureEditor } from "./useFeatureEditor";

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
              <option value="select">컬럼 선택</option>
              <option value="filter_not_null">결측 행 제외</option>
              <option value="fill_null">결측값 대체</option>
              <option value="cast">Float64 변환</option>
              <option value="lag">Lag 1</option>
              <option value="rolling_mean">Rolling Mean 3</option>
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
          <div className="form-row">
            <label>시간·정렬 컬럼 <select aria-label="시간·정렬 컬럼" value={orderBy} onChange={e => setOrderBy(e.target.value)}>
              <option value="">정렬하지 않음</option>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <label>그룹 컬럼 (최대 4개) <select aria-label="그룹 컬럼" multiple size={Math.min(5, Math.max(2, columnNames.length))}
              value={groupBy} onChange={e => setGroupBy(Array.from(e.target.selectedOptions, option => option.value)
                .filter(Boolean).slice(0, 4))}>
              <option value="">그룹 없음</option>
              {columnNames.filter(c => c !== orderBy).map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <p className="hint">Lag / Rolling은 시간 정렬 후 그룹별 계산합니다. Rolling Mean에는 현재 행이 포함됩니다.</p>
          </div>
          <div className="steps">{steps.length === 0 && <p className="muted">아직 변환 단계가 없습니다.</p>}
            {steps.map((step, i) => <div className="step" key={i}>
              <strong>{i + 1}. {step.op}</strong>
              <span>{step.column} {step.periods ? `· 이전 ${step.periods}행` : ""}
                {step.window ? `· ${step.window}행` : ""}</span>
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
            onClick={() => void previewFeatures()}>샘플 변환 확인</button>
            {canRun && <button disabled={busy || !steps.length} onClick={onSubmit}>
              전체 변환 Job 실행 →
            </button>}</div>
          {featurePreview && <div className="result"><h4>변환 미리보기 · 전체 입력 기준 계산</h4>
             <p className="hint">결과는 최대 30행입니다. 단순 변환은 큰 Dataset에서도 일부 결과만 읽고, 전역 Window는 동기 예산 초과 시 전체 Job을 안내합니다.</p>
            <div className="table-scroll"><table><thead><tr>{Object.entries(featurePreview.schema).map(
              ([c, t]) => <th key={c}>{c}<small>{t}</small></th>
            )}</tr></thead><tbody>{featurePreview.rows.slice(0, 15).map((r, i) => <tr key={i}>
              {Object.keys(featurePreview.schema).map(c => <td key={c}>{r[c] == null ? "—" : String(r[c])}</td>)}
            </tr>)}</tbody></table></div></div>}
        </section>
  );
}
