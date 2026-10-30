/** Pipeline 후보 한 건의 관측값과 명시적으로 계산한 설명 결과를 분석한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { FeatureResultDetails } from "./FeatureResultDetails";
import type { MLResourceKind } from "./PlatformNavigation";
import type { PipelineCandidates, ModelRevision } from "./types";
import { ShapExplanation } from "./ShapExplanation";
import { ActivityProfile } from "./ActivityProfile";
import { formatObservedAt } from "./display-format";

type Candidate = PipelineCandidates["items"][number];

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function format(value: unknown): string {
  const num = numeric(value);
  return num === null ? "미정의" :
    num.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
}

export function ObservationInvestigation({
  candidate, scoringResultId, modelRevisionId, canRun, canCancel, onOpenResource,
  observationMode = false, scoreOrder, isDefaultObservation = false, onTryObservation,
}: {
  candidate: Candidate; scoringResultId: string; modelRevisionId: string | null;
  scoreOrder?: number;
  canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  observationMode?: boolean;
  isDefaultObservation?: boolean;
  onTryObservation?: (
    features: Record<string, number | null>, rowIndex: number, score: number,
  ) => string | null;
}) {
  const [model, setModel] = useState<ModelRevision | null>(null);
  const [modelError, setModelError] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [modelReload, setModelReload] = useState(0);
  useEffect(() => {
    if (!modelRevisionId) return;
    let active = true;
    setModel(null); setModelError("");
    void request<ModelRevision>("/api/ml/models/" + encodeURIComponent(modelRevisionId))
      .then(next => {
        if (!active) return;
        if (next.id !== modelRevisionId) setModelError("선택한 분석과 모델이 일치하지 않습니다.");
        else setModel(next);
      })
      .catch(reason => { if (active) setModelError(formatError(reason)); });
    return () => { active = false; };
  }, [modelRevisionId, modelReload]);
  const medians = model?.runtime?.feature_reference_medians;
  const referenceMap = model && Array.isArray(medians) &&
    medians.length === model.features.length &&
    medians.every(v => typeof v === "number" && Number.isFinite(v))
    ? new Map(model.features.map((name, i) => [name, medians[i] as number])) : null;
  const [shapVisited, setShapVisited] = useState(false);
  const missing = Object.entries(candidate.features).filter(([, value]) => value === null || !Number.isFinite(value));
  function referenceLabel(name: string): string {
    if (referenceMap?.has(name)) return format(referenceMap.get(name));
    if (!modelRevisionId) return "모델 판본 없음";
    if (modelError) return "조회 오류";
    if (!model) return "조회 중";
    return "비교 기준 없음";
  }
  function referenceDifference(name: string, value: number | null): string | null {
    const reference = referenceMap?.get(name);
    if (typeof value !== "number" || !Number.isFinite(value) ||
        typeof reference !== "number" || !Number.isFinite(reference)) return null;
    const difference = value - reference;
    if (!Number.isFinite(difference)) return null;
    if (difference === 0) return "학습 중앙값과 같음";
    const magnitude = Math.abs(difference);
    const amount = magnitude < 0.000001 ? magnitude.toExponential(2) : format(magnitude);
    return `학습 중앙값 대비 ${difference > 0 ? "+" : "−"}${amount}`;
  }
  return <section className="observation-inspection"
    aria-label={observationMode
      ? isDefaultObservation ? "기본 표시 관측값 분석" : "선택한 관측값 분석"
      : "선택한 후보 분석"}>
    <div className="observation-title">
      <h5 id="observation-detail-heading" tabIndex={-1}>{observationMode ? isDefaultObservation ? "첫 번째 관측값" : "선택한 관측값" : "선택한 검토 대상"}</h5>
      {typeof scoreOrder === "number" && <span>점수순 {scoreOrder.toLocaleString("ko-KR")}번째</span>}
    </div>
    <div className="observation-score"><span>모델 이상 점수</span><strong>{format(candidate.anomaly_score)}</strong></div>
    {!candidate.observed_at && !candidate.observation_id
      ? <p className="observation-source-missing">관측 시각·원본 관측치 ID 없음
          <strong>결과 내부 행 #{candidate.row_index.toLocaleString("ko-KR")}</strong>
          <small>이 번호는 해당 Scoring Result에서만 유효하며 원본 이벤트의 ID가 아닙니다.</small>
        </p>
      : <p className="observation-meta">
            {candidate.observed_at && <span title={candidate.observed_at}>
              관측 시각 <strong>{formatObservedAt(candidate.observed_at)}</strong></span>}
            {candidate.observation_id && <span>원본 관측치 ID <strong>{candidate.observation_id}</strong></span>}
            <span>결과 행 #{candidate.row_index.toLocaleString("ko-KR")}</span>
            {!candidate.observation_id && <span>원본 관측치 ID 없음</span>}
          </p>}
    {observationMode && onTryObservation && canRun && <div className="observation-try-experiment">
      <button type="button" className="secondary" onClick={() => {
        setExperimentError(onTryObservation(candidate.features, candidate.row_index,
          candidate.anomaly_score) ?? "");
      }}>이 관측값으로 실험 →</button>
      {experimentError && <p role="alert">{experimentError}</p>}
    </div>}
    <section className="observation-activity" aria-label="관측된 활동 특징">
      {model && <ActivityProfile model={model} values={candidate.features} />}
      {missing.length > 0 && <p className="hint">비어 있거나 유효한 숫자가 아닌 입력 {missing.length}개가 있습니다. 실제 값과 학습 시 대체 기준을 확인하세요.</p>}
      {!modelRevisionId && <p className="hint">학습 모델 판본 참조가 없어 중앙값을 비교할 수 없습니다.</p>}
      {modelRevisionId && !model && !modelError &&
        <p role="status">학습 참조 중앙값을 조회하는 중입니다.</p>}
      {modelError && <div className="notice-blocked" role="alert">
        <p>학습 중앙값 조회 실패: {modelError}</p>
        <button type="button" className="secondary" onClick={() => setModelReload(n => n + 1)}>
          학습 참조값 다시 조회
        </button>
      </div>}
      {model && !referenceMap && <p className="hint">이 모델에는 비교 가능한 학습 중앙값이 없습니다.</p>}
      <FeatureResultDetails key={scoringResultId + ":" + candidate.row_index}
        summary={`입력값 확인 · ${Object.keys(candidate.features).length}개 항목`}
        columns={["관측값", "학습 중앙값", "기준값 대비"]}
        rows={Object.entries(candidate.features).map(([name, value]) => ({ feature: name,
          values: [format(value), referenceLabel(name), referenceDifference(name, value) ?? "비교 불가"],
        }))} />
    </section>
    <details className="observation-secondary-analysis" onToggle={event => {
      if (event.currentTarget.open) setShapVisited(true);
    }}>
      <summary>모델 점수 설명 · SHAP</summary>
      {shapVisited && <ShapExplanation candidate={candidate} scoringResultId={scoringResultId}
        modelRevisionId={modelRevisionId} canRun={canRun} canCancel={canCancel}
        onOpenResource={onOpenResource} />}
    </details>
  </section>;
}
