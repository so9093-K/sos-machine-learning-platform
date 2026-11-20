import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { algorithmLabel, modelVersionLabel } from "./display-format";
import { MAX_TRANSFORM_STEPS, type FeatureStep } from "./feature-utils";
import { checkModelDatasetInput, ModelDatasetCompatibility, PREPARATION_CAST_DTYPE,
  preparationCastable } from "./ModelDatasetCompatibility";
import type { DatasetColumns, ModelRevision } from "./types";

type Columns = DatasetColumns["columns"];
type InputModel = Pick<ModelRevision, "features" | "runtime">;

/** 미리보기·변환 결과 스키마를 입력 검사에 쓰는 컬럼 목록으로 바꾼다. */
export function schemaColumns(schema: Record<string, string>): Columns {
  return Object.entries(schema).map(([name, dtype]) => ({ name, dtype }));
}

/** 데이터 준비로 맞출 수 있지만 아직 변환 목록에 없는 단계. 이름 변경을 먼저 두어,
 *  이름과 자료형이 모두 다른 컬럼은 바꾼 이름으로 실수형 변환한다. */
export function pendingInputSteps(model: InputModel, columns: Columns, steps: FeatureStep[]): FeatureStep[] {
  const input = checkModelDatasetInput(model, columns);
  const hasCast = (column: string) => steps.some(step => step.op === "cast" && step.column === column &&
    step.dtype === PREPARATION_CAST_DTYPE);
  const renames: FeatureStep[] = input.renamed
    .filter(item => !steps.some(step => step.op === "rename" && step.column === item.actual))
    .map(item => ({ op: "rename", column: item.actual, alias: item.name }));
  const casts: FeatureStep[] = [
    ...input.castable.map(item => item.name),
    ...input.renamed.filter(item => item.actualDtype !== item.expected &&
      preparationCastable(item.actualDtype, item.expected)).map(item => item.name),
  ].filter(name => !hasCast(name))
    .map(name => ({ op: "cast", column: name, dtype: PREPARATION_CAST_DTYPE }));
  return [...renames, ...casts];
}

/** 데이터 화면이 맞추고 있는 모델. 조회 실패는 문맥 해제와 별개로 다시 시도할 수 있다. */
export function useModelRevision(id?: string) {
  const [state, setState] = useState<{ id: string; model: ModelRevision | null; error: string } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!id) return;
    let active = true;
    setState(null);
    void request<ModelRevision>(`/api/ml/models/${encodeURIComponent(id)}`)
      .then(model => { if (active) setState({ id, model, error: "" }); })
      .catch(cause => { if (active) setState({ id, model: null, error: formatError(cause) }); });
    return () => { active = false; };
  }, [id, retry]);
  const current = state?.id === id ? state : null;
  return { model: current?.model ?? null, error: current?.error ?? "",
    loading: Boolean(id) && !current, retry: () => setRetry(value => value + 1) };
}

/** 모델 적용에서 데이터 화면으로 온 목적을 유지하고, 준비한 Dataset으로 돌아가는 길을 보인다. */
export function ModelInputBanner({ model, loading, error, onRetry, columns, datasetSelected,
  waitingForJob, onReturn, onDismiss }: {
  model: ModelRevision | null; loading: boolean; error: string; onRetry: () => void;
  /** 선택 Dataset의 현재 스키마. 미리보기가 없는 화면에서는 생략한다. */
  columns?: Columns;
  datasetSelected: boolean;
  /** 작업 이력처럼 결과 Dataset이 아직 선택되지 않은 화면. */
  waitingForJob: boolean;
  onReturn: () => void; onDismiss: () => void;
}) {
  const check = model && columns ? checkModelDatasetInput(model, columns) : null;
  const blockers = check ? check.missing.length + check.mismatches.length : 0;
  return <section className="model-input-banner" aria-label="모델 입력 맞추기">
    <div>
      <span className="eyebrow">모델 입력 맞추기</span>
      {model && <strong>{algorithmLabel(model.algorithm)} · {modelVersionLabel(model)}</strong>}
      {loading && <p role="status" className="hint">적용할 모델을 확인하는 중입니다.</p>}
      {error && <p role="alert">적용할 모델을 확인하지 못했습니다. {error}
        <button type="button" className="ghost" onClick={onRetry}>다시 조회</button></p>}
      <p className="hint">{waitingForJob
        ? "변환이 끝나면 결과 Dataset을 열어 입력 구조를 확인한 뒤 모델 적용으로 돌아갑니다."
        : "이 모델에 적용할 Dataset을 준비하고 있습니다. 준비한 Dataset을 선택한 뒤 모델 적용으로 돌아갑니다."}</p>
      {check && <p className={check.ready ? "scoring-input-ready" : "hint"} role="status">{check.ready
        ? "선택한 Dataset 입력 구조 일치"
        : `선택한 Dataset 입력 구조 불일치 · ${blockers}개`}</p>}
    </div>
    <div className="model-input-banner-actions">
      {!waitingForJob && <button type="button" className="secondary" disabled={!model || !datasetSelected}
        onClick={onReturn}>선택한 Dataset으로 모델 적용 →</button>}
      <button type="button" className="ghost" onClick={onDismiss}>맞추기 종료</button>
    </div>
  </section>;
}

/** 데이터 준비에서 모델 입력 조건을 확인하고, 데이터 준비로 해결할 수 있는 변환만 초안에 더한다.
 *
 *  자동 실행·자동 보정은 하지 않는다. 추가한 단계는 변환 목록에서 확인·수정하고,
 *  변환 미리보기 스키마로 결과가 입력 조건과 맞는지 실행 전에 다시 확인한다.
 */
export function ModelInputPreparation({ model, columns, steps, transformed, onAddSteps }: {
  model: ModelRevision; columns: Columns; steps: FeatureStep[];
  /** 현재 변환 단계의 미리보기 스키마. 미리보기 전이거나 단계가 바뀌면 null이다. */
  transformed: Columns | null;
  onAddSteps: (steps: FeatureStep[]) => void;
}) {
  const current = checkModelDatasetInput(model, columns);
  const pending = pendingInputSteps(model, columns, steps);
  const capacity = Math.max(0, MAX_TRANSFORM_STEPS - steps.length);
  const after = transformed ? checkModelDatasetInput(model, transformed) : null;
  return <section className="panel model-input-preparation" aria-label="모델 입력 조건 맞추기">
    <div className="panel-head"><div><h3>모델 입력 조건 맞추기</h3>
      <p>변환 결과가 모델의 Feature 이름·자료형과 맞는지 미리보기로 확인한 뒤 전체 변환을 실행합니다.</p>
    </div></div>
    {current.ready
      ? <p className="scoring-input-ready" role="status">현재 Dataset 입력 구조 일치 · 변환 없이 모델에 적용할 수 있습니다.</p>
      : after?.ready
        // 미리보기로 해결이 확인되면 결과를 먼저 보이고, 원본의 불일치 내역은 펼쳐 확인한다.
        ? <details className="input-fix-more">
          <summary>원본 Dataset의 불일치 {current.missing.length + current.mismatches.length}개 보기</summary>
          <ModelDatasetCompatibility model={model} columns={columns} />
        </details>
        : <ModelDatasetCompatibility model={model} columns={columns} />}
    {pending.length > 0 && <div className="model-input-actions">
      <button type="button" className="secondary" disabled={capacity === 0}
        onClick={() => onAddSteps(pending.slice(0, capacity))}>
        필요한 변환 단계 {Math.min(pending.length, capacity)}개 추가</button>
      <p className="hint">{pending.length > capacity
        ? `한 번의 변환은 최대 ${MAX_TRANSFORM_STEPS}단계입니다. 나머지 ${pending.length - capacity}개는 변환 결과 Dataset에서 이어서 변환합니다.`
        : "추가한 단계는 아래 변환 목록에서 확인·수정할 수 있습니다."}</p>
    </div>}
    {!current.ready && steps.length > 0 && (after
      ? <p className={after.ready ? "scoring-input-ready" : "hint"} role="status">{after.ready
        ? "변환 미리보기 기준 입력 구조 일치 · 전체 변환을 실행하면 결과 Dataset을 이 모델에 적용할 수 있습니다."
        : `변환 미리보기 기준 입력 구조 불일치 · ${after.missing.length + after.mismatches.length}개`}</p>
      : <p className="hint">변환 결과 미리보기로 입력 구조를 다시 확인할 수 있습니다.</p>)}
  </section>;
}
