import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { formatFeatureValue } from "./FeatureValueComparison";
import { FeatureResultDetails } from "./FeatureResultDetails";
import { ModelScoreContext } from "./ModelScoreContext";
import type { MLPlaygroundJob, ModelRevision } from "./types";

export type PlaygroundComparison = {
  modelId: string;
  label: string;
  fields: Record<string, number>;
  score: number | null;
};
export const formatPlaygroundScore = (value: number) => value.toFixed(6);

/** 표시 반올림과 계산값의 일치를 구분하며 모델 반응의 이유는 추측하지 않는다. */
export function playgroundScoreChange(before: number, after: number): string {
  const delta = after - before;
  if (delta === 0) return "계산된 점수는 같습니다.";
  if (formatPlaygroundScore(before) === formatPlaygroundScore(after)) return "차이가 있지만 소수 6자리 표시에서는 같습니다.";
  return delta > 0 ? "변경한 입력에 모델이 더 높은 이상 점수를 매겼습니다."
    : "변경한 입력에 모델이 더 낮은 이상 점수를 매겼습니다.";
}

export function PlaygroundResult({ model, job, comparison, matchesInput, onEdit, onRestore }: {
  model: ModelRevision; job: MLPlaygroundJob;
  comparison: PlaygroundComparison | null;
  matchesInput: boolean;
  onEdit: (feature?: string) => void;
  onRestore: (fields: Record<string, number>) => void;
}) {
  const score = job.result?.anomaly_score;
  if (job.status !== "Succeeded" || typeof score !== "number" || !Number.isFinite(score) ||
      job.spec.model_revision_id !== model.id) return null;
  const fields = job.spec.features as Record<string, number>;
  const anchor = comparison?.modelId === model.id ? comparison : null;
  const changed = anchor ? model.features.filter(name => fields[name] !== anchor.fields[name]) : [];
  const delta = anchor?.score == null ? null : score - anchor.score;
  return <section className="playground-result" aria-label="입력 변경과 모델 반응">
    <h4>{anchor ? changed.length ? `입력 ${changed.length}개를 바꾼 결과` : anchor.score === null ? "학습 기준 입력의 점수" : "같은 입력의 점수 확인" : "이번 입력의 계산 결과"}</h4>
    {!matchesInput && <p className="result-stale" role="status">입력값을 편집 중입니다. 아래는 마지막으로 계산한 입력의 결과입니다.</p>}
    <div className="experiment-score-comparison" aria-live="polite">
      {anchor?.score != null && <div><span>{anchor.label}</span><strong>{formatPlaygroundScore(anchor.score)}</strong></div>}
      <div><span>이번 이상 점수</span><strong>{formatPlaygroundScore(score)}</strong></div>
      {delta !== null && <div><span>점수 차이</span><strong title={String(delta)}>
        {`${delta > 0 ? "+" : ""}${delta !== 0 && Math.abs(delta) < 0.0000005 ? delta.toExponential(2) : formatPlaygroundScore(delta)}`}</strong></div>}
    </div>
    {anchor?.score != null && <p className="experiment-finding">{changed.length === 0 && delta === 0
      ? "같은 모델과 입력으로 비교 대상과 같은 점수를 확인했습니다. 값을 바꾼 실험은 아직 하지 않았습니다."
      : changed.length === 0 ? "같은 입력인데 저장된 점수와 다릅니다. 원본 Job과 모델 판본의 계산 근거를 확인하세요."
      : playgroundScoreChange(anchor.score, score)}</p>}
    {anchor && anchor.score === null && <p className="experiment-finding">{changed.length
      ? "학습 초기 입력에서 값을 바꿨습니다. 초기 입력의 계산 기록이 없어 점수 차이는 비교하지 않습니다."
      : "학습 Feature별 중앙값으로 준비한 입력입니다. 실제 관측값을 그대로 재현한 입력은 아닙니다."}</p>}
    {anchor && changed.length > 0 && <div className="experiment-input-changes">
      <p>{anchor.label}에서 바꾼 입력 {changed.length}개</p>
      <ul>{changed.slice(0, 3).map(name => <li key={name}>
        <button type="button" className="ghost" onClick={() => onEdit(name)}>{activityFeatureLabel(name)}</button>
        <span>{formatFeatureValue(name, anchor.fields[name])} → <strong>{formatFeatureValue(name, fields[name])}</strong></span>
      </li>)}</ul>
      {changed.length > 3 && <FeatureResultDetails summary={`나머지 수정 항목 확인 · ${changed.length - 3}개`}
        columns={["비교 대상", "이번 입력"]} rows={changed.slice(3).map(name => ({ feature: name,
          values: [formatFeatureValue(name, anchor.fields[name]), formatFeatureValue(name, fields[name])],
        }))} />}
    </div>}
    <ModelScoreContext model={model} score={score} comparisonScore={anchor?.score} />
    <div className="experiment-next-actions">
      <button type="button" className="secondary" onClick={() => onEdit()}>수정 항목 선택</button>
      {anchor && changed.length > 0 && <button type="button" className="ghost" onClick={() => onRestore(anchor.fields)}>
        {anchor.label} 복원</button>}
    </div>
  </section>;
}
