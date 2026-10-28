/** 첫 등록형 Pipeline을 불필요한 사전 학습 없이 실제 실행하도록 안내한다. */
import type { Example, PipelineDefinition } from "./types";

type Phase = "registering" | "checking" | "submitting" | null;

export function ModelLabIntroduction({ definition, example, examplesLoading,
  exampleBusy, examplePhase, canCreateDataset, canRun, catalogError, onRetryCatalog,
  examplesError, onRetryExamples, onStartExample, onOwnData }: {
  definition: PipelineDefinition | null;
  example?: Example;
  examplesLoading: boolean;
  exampleBusy: boolean;
  examplePhase: Phase;
  canCreateDataset: boolean;
  canRun: boolean;
  catalogError: string;
  onRetryCatalog: () => void;
  examplesError?: string;
  onRetryExamples?: () => void;
  onStartExample: () => void;
  onOwnData: () => void;
}) {
  const isReady = Boolean(definition && example && !examplesLoading &&
    canCreateDataset && canRun && !exampleBusy);
  return <section className="platform-intro pipeline-hero pipeline-first-entry"
    aria-labelledby="pipeline-first-entry-heading">
    <div>
      <span className="eyebrow">모델 실험실 · 활동 이상 탐지</span>
      <h2 id="pipeline-first-entry-heading">예제 데이터로 모델 학습</h2>
      <p>준비된 활동 데이터로 Isolation Forest 모델을 학습하고,
        다른 시간 구간의 이상 후보까지 확인합니다.</p>
    </div>
    <div className="pipeline-first-actions">
      <button id="pipeline-example-start" type="button" disabled={!isReady}
        onClick={onStartExample}>
        {examplePhase === "registering" ? "예제 데이터 준비 중…" :
         examplePhase === "checking" ? "학습 입력 확인 중…" :
         examplePhase === "submitting" ? "모델 학습 요청 중…" :
         "예제로 모델 학습 시작 →"}
      </button>
      <button type="button" className="secondary" disabled={exampleBusy}
        onClick={onOwnData}>내 Dataset으로 시작</button>
    </div>
    {examplePhase && <p role="status" className="pipeline-first-status">{
      examplePhase === "registering" ? "예제 Dataset을 준비하고 있습니다." :
      examplePhase === "checking" ? "학습 입력을 확인하고 있습니다." :
      "학습 실행을 제출하고 있습니다."
    }</p>}
    {!definition && <div role={catalogError ? "alert" : "status"} className="pipeline-first-notice">
      {catalogError ? <><span>분석 방법을 확인할 수 없습니다.</span>
        <button type="button" className="secondary" onClick={onRetryCatalog}>
          다시 조회</button></> : "분석 방법을 확인하고 있습니다."}
    </div>}
    {!examplesLoading && !example && <div role="alert" className="pipeline-first-notice">
      <span>{examplesError ? "예제 목록을 불러오지 못했습니다." : "현재 실행할 예제가 없습니다."}</span>
      {examplesError && onRetryExamples && <button type="button" className="secondary"
        onClick={onRetryExamples}>예제 다시 조회</button>}
    </div>}
    {examplesLoading && <p role="status" className="pipeline-first-notice">예제를 확인하고 있습니다.</p>}
    {(!canCreateDataset || !canRun) && <p className="pipeline-first-notice">
      {!canCreateDataset ? "예제 Dataset 등록 권한" : "Job 실행 권한"}이 없어 실행할 수 없습니다.
    </p>}
    <details className="pipeline-first-details">
      <summary>예제와 분석 방법</summary>
      {example && <p>{example.title} · {example.row_count.toLocaleString("ko-KR")}개 합성 관측치</p>}
      {definition && <p>학습 방법: Isolation Forest · 입력: {definition.required_features.length}개 수치형
        Feature와 {definition.required_time_column} 시간 컬럼</p>}
      <p>모델 학습 후 별도 시간 구간의 점수를 계산하고, 판정 기준에 따른 검토 후보를 확인합니다.</p>
    </details>
  </section>;
}
