import { useEffect, useState, type ReactNode } from "react";
import { ApiError, formatError, request } from "./api";
import { DataChart } from "./DataChart";
import { verifiedScoreHistogram } from "./ModelScoreContext";
import { EvaluationSummary } from "./EvaluationSummary";
import { ObservationInvestigation } from "./ObservationInvestigation";
import { formatRecordedAt, jobStatusLabel } from "./display-format";
import { isLocalOperatorHost, mlflowRunUrl } from "./operator-tools";
import type { MLResourceKind } from "./PlatformNavigation";
import type { DecisionPolicyRevision, EvaluationRevision, MLflowExportStatus,
  ModelRevision, PipelineCandidates, PipelineRun, ScoringResult } from "./types";

export const PIPELINE_STAGES = [
  ["preflight", "입력·분할"], ["training", "학습"], ["calibration", "보정 점수"],
  ["policy", "판정 기준"], ["test", "Test 점수"], ["evaluation", "평가"],
] as const;

export function pipelineResultId(run: PipelineRun, stage: string, field: string): string | null {
  const job = run.stages?.find(item => item.name === stage)?.job;
  const value = job?.status === "Succeeded" ? job.result?.[field] : null;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function amount(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 6 }) : "기록 없음";
}

/** 결과별 조회 실패를 격리하고, 실행을 바꾸면 과거 판본을 즉시 감춘다. */
function useAsset<T>(path: string | null, revision: number) {
  const [state, setState] = useState<{ path: string; data?: T; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!path) return;
    let active = true;
    setState(null);
    void request<T>(path).then(data => { if (active) setState({ path, data }); })
      .catch(error => { if (active) setState({ path, error: formatError(error) }); });
    return () => { active = false; };
  }, [path, revision, retry]);
  return { data: state?.path === path ? state.data : undefined,
    error: state?.path === path ? state.error : undefined,
    retry: () => setRetry(value => value + 1) };
}

function AssetState({ error, onRetry, children }: {
  error?: string; onRetry: () => void; children?: ReactNode;
}) {
  return error ? <div role="alert"><p>결과 조회 실패 · {error}</p>
    <button className="secondary" onClick={onRetry}>결과 다시 조회</button></div>
    : children ?? <p role="status">결과 조회 중…</p>;
}

function TrackingLink({ jobId, label, revision }: { jobId: string; label: string; revision: number }) {
  const result = useAsset<MLflowExportStatus>(`/api/ml/tracking/jobs/${encodeURIComponent(jobId)}`, revision);
  const labels = { Disabled: "기록 미설정", Pending: "기록 대기", Sending: "기록 전송 중",
    Retry: "기록 재시도 대기", Succeeded: "기록 완료" };
  return <div className="pipeline-tracking">
    <AssetState error={result.error} onRetry={result.retry}>
      {result.data && <>
        <span>{label} · {labels[result.data.state]}</span>
        {result.data.run_id && <a href={mlflowRunUrl(result.data.run_id)} target="_blank"
          rel="noopener noreferrer">{label} MLflow Run ↗</a>}
        {result.data.last_error_code && <small>기록 오류 · {result.data.last_error_code}</small>}
      </>}
    </AssetState>
  </div>;
}

function ScoreSummary({ result, threshold }: { result: ScoringResult; threshold?: number }) {
  const histogram = verifiedScoreHistogram(result.score_summary, result.row_count);
  return <>
    <p>관측값 {amount(result.row_count)}건 · 평균 점수 {amount(result.score_summary.score_mean)}</p>
    {histogram ? <DataChart ariaLabel="보존된 전체 이상 점수 분포" option={{
      grid: { left: 50, right: 20, top: 30, bottom: 50 },
      tooltip: { trigger: "item" },
      xAxis: { type: "category", name: "이상 점수 구간", nameLocation: "middle", nameGap: 30,
        data: histogram.counts.map((_, i) => `${amount(histogram.edges[i])}–${amount(histogram.edges[i + 1])}`),
        axisLabel: { hideOverlap: true } },
      yAxis: { type: "value", name: "관측값 수", minInterval: 1 },
      series: [{ type: "bar", data: histogram.counts }],
    }} /> : <p className="hint">검증 가능한 전체 점수 분포가 보존돼 있지 않습니다.</p>}
    {threshold !== undefined && <p>Calibration 기준 임계값 {amount(threshold)} · 점수 ≥ 임계값인 관측값을 이상 후보로 판정</p>}
  </>;
}

function CandidateInvestigation({ runId, modelId, canRun, canCancel, onOpenResource }: {
  runId: string; modelId: string | null; canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const page = useAsset<PipelineCandidates>(open ? `/api/pipeline-runs/${encodeURIComponent(runId)}/candidates?limit=5` +
    (cursor ? `&cursor=${encodeURIComponent(cursor)}` : "") : null, 0);
  const selected = page.data?.items.find(item => item.row_index === selectedRow);
  return <section className="panel pipeline-candidates">
    <details onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>이상 후보 조사</summary>
      <p className="hint">판정 임계값을 적용한 점수순 후보 · 한 번에 최대 5건</p>
      {open && <AssetState error={page.error} onRetry={page.retry}>{page.data && <>
        {page.data.items.length === 0 && <p>현재 조회 구간에 이상 후보가 없습니다.</p>}
        <ul className="pipeline-candidate-list">{page.data.items.map(item => <li key={item.row_index}>
          <button className="secondary" aria-pressed={selectedRow === item.row_index} onClick={() => setSelectedRow(item.row_index)}>
            <strong>이상 점수 {amount(item.anomaly_score)}</strong>
            <span>{item.observed_at ? formatRecordedAt(item.observed_at) : "관측 시각 기록 없음"} · 결과 행 {item.row_index}</span>
          </button>
        </li>)}</ul>
        <div className="pipeline-actions">
          {cursor && <button className="secondary" onClick={() => { setCursor(null); setSelectedRow(null); }}>상위 후보</button>}
          {page.data.next_cursor && <button className="secondary" onClick={() => {
            setCursor(page.data!.next_cursor!); setSelectedRow(null);
          }}>다음 후보 5건</button>}
        </div>
        {selected && <ObservationInvestigation key={`${runId}:${selected.row_index}`} candidate={selected}
          scoringResultId={page.data.result_id} modelRevisionId={modelId} canRun={canRun} canCancel={canCancel}
          onOpenResource={onOpenResource} />}
      </>}</AssetState>}
    </details>
  </section>;
}

/** SOS의 확정 결과를 요약하고 전문 조사는 기존 판본 화면과 MLflow로 연결한다. */
export function PipelineResults({ run, revision, canRun, canCancel, canOpenMLflow, onOpenResource, onOpenDataset }: {
  run: PipelineRun; revision: number; canRun: boolean; canCancel: boolean; canOpenMLflow: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onOpenDataset: (id: string) => void;
}) {
  const modelId = pipelineResultId(run, "training", "model_revision_id");
  const calibrationId = pipelineResultId(run, "calibration", "scoring_result_id");
  const policyId = pipelineResultId(run, "policy", "policy_revision_id");
  const testId = pipelineResultId(run, "test", "scoring_result_id");
  const evaluationId = pipelineResultId(run, "evaluation", "evaluation_revision_id");
  const path = (base: string, id: string | null) => id ? `/api/ml/${base}/${encodeURIComponent(id)}` : null;
  const model = useAsset<ModelRevision>(path("models", modelId), revision);
  const calibration = useAsset<ScoringResult>(path("scoring/results", calibrationId), revision);
  const policy = useAsset<DecisionPolicyRevision>(path("policies", policyId), revision);
  const test = useAsset<ScoringResult>(path("scoring/results", testId), revision);
  const evaluation = useAsset<EvaluationRevision>(path("evaluations", evaluationId), revision);
  const plan = run.input_plan;
  const tracking = (stage: string) => {
    const job = run.stages?.find(item => item.name === stage)?.job;
    return canOpenMLflow && isLocalOperatorHost() && job
      ? <TrackingLink jobId={job.id} label={PIPELINE_STAGES.find(([name]) => name === stage)?.[1] ?? stage} revision={revision} /> : null;
  };
  const open = (kind: MLResourceKind, id: string, label: string) =>
    <button className="secondary" onClick={() => onOpenResource(kind, id)}>{label} →</button>;
  return <div className="pipeline-results">
    {evaluation.data && <div className="pipeline-outcome" role="status">
      <strong>Test 평가</strong>
      <span>관측값 {amount(evaluation.data.decision_metrics.score_count)}건</span>
      <span>이상 판정 {amount(evaluation.data.decision_metrics.predicted_anomalies)}건</span>
      <span>판정 비율 {typeof evaluation.data.decision_metrics.alert_rate === "number"
        ? `${amount(evaluation.data.decision_metrics.alert_rate * 100)}%` : "기록 없음"}</span>
      <span className="hint">{evaluation.data.label_coverage === "none" ? "정답 레이블 없음 · 탐지 정확도 미확인" : "평가 조건은 아래 평가 근거에서 확인"}</span>
    </div>}
    {run.status === "Succeeded" && testId && policyId && evaluationId && <CandidateInvestigation
      runId={run.id} modelId={modelId} canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource} />}
    <section className="panel">
      <h3>입력·시간 분할</h3>
      {plan ? <>
        <div className="pipeline-partitions">
          <div><strong>Train</strong><span>{amount(plan.partition_sizes.train)}건</span></div>
          <div><strong>Calibration</strong><span>{amount(plan.partition_sizes.calibration)}건</span></div>
          <div><strong>Test</strong><span>{amount(plan.partition_sizes.test)}건</span></div>
        </div>
        <details><summary>시간 분할 근거</summary>
          <p>시간 컬럼 {plan.timestamp_column} · 시간순 분할</p>
          <p>Train: {formatRecordedAt(plan.train_end)} 미만</p>
          <p>Calibration: Train 종료 이상 · {formatRecordedAt(plan.calibration_end)} 미만</p>
          <p>Test: Calibration 종료 이상</p>
        </details>
      </> : <p className="hint">입력 검증이 확정한 시간 분할 근거가 아직 없습니다.</p>}
      <button className="secondary" onClick={() => onOpenDataset(run.dataset_id)}>입력 Dataset →</button>
    </section>
    {modelId && <section className="panel">
      <h3>학습 결과</h3>
      <AssetState error={model.error} onRetry={model.retry}>{model.data && <>
        <p>Isolation Forest · 학습 점수 {amount(model.data.training_score_summary.score_count)}건
          · Feature {model.data.features.length}개</p>
        <p>{model.data.registry_version ? `Registry v${model.data.registry_version} · 등록 확인` : "학습 완료 · Registry 등록 미확인"}</p>
        <p className="hint">학습 점수는 Train 내부 진단이며 독립 평가 성능이 아닙니다. 모델 제공은 별도 절차입니다.</p>
        {open("model", modelId, "모델 판본")}
        <details><summary>학습 설정</summary><pre>{JSON.stringify(model.data.params, null, 2)}</pre></details>
      </>}</AssetState>
      {tracking("training")}
    </section>}
    {(calibrationId || policyId) && <section className="panel">
      <h3>보정 점수·판정 기준</h3>
      {calibrationId && <AssetState error={calibration.error} onRetry={calibration.retry}>
        {calibration.data && <ScoreSummary result={calibration.data} />}
      </AssetState>}
      {policyId && <AssetState error={policy.error} onRetry={policy.retry}>
        {policy.data && <>
          <p>임계값 {amount(policy.data.threshold)}
            {policy.data.alert_fraction != null ? ` · 설정 경보 비율 ${amount(policy.data.alert_fraction * 100)}%` : ""}</p>
          <p className="hint">Calibration 점수로 정한 판정 기준입니다. 동점에 따라 실제 판정 비율은 설정값과 다를 수 있습니다.</p>
          {open("policy", policyId, "판정 정책")}
        </>}
      </AssetState>}
      {calibrationId && open("scoring", calibrationId, "보정 점수 조사·별도 정책 생성")}
      {canOpenMLflow && isLocalOperatorHost() && <details><summary>보정·판정 MLflow 기록</summary>
        {tracking("calibration")}{tracking("policy")}</details>}
    </section>}
    {(testId || evaluationId) && <section className="panel">
      <h3>Test 결과·평가</h3>
      {testId && <AssetState error={test.error} onRetry={test.retry}>
        {test.data && <ScoreSummary result={test.data} threshold={policy.data?.threshold} />}
      </AssetState>}
      {evaluationId && <AssetState error={evaluation.error} onRetry={evaluation.retry}>
        {evaluation.data && <details><summary>평가 조건·지표</summary><EvaluationSummary evaluation={evaluation.data} /></details>}
      </AssetState>}
      <p className="hint">이상 후보는 실제 사건이나 탐지 정확도의 증명이 아닙니다. 추가 정책·평가는 별도 결과로 남습니다.</p>
      {testId && open("scoring", testId, "Test 관측값 조사")}
      {evaluationId && open("evaluation", evaluationId, "평가 근거")}
      {canOpenMLflow && isLocalOperatorHost() && <details><summary>Test·평가 MLflow 기록</summary>
        {tracking("test")}{tracking("evaluation")}</details>}
    </section>}
  </div>;
}

export function PipelineRunDetails({ runId, canRun, canCancel, canOpenMLflow, onOpenResource, onOpenDataset }: {
  runId: string; canRun: boolean; canCancel: boolean; canOpenMLflow: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onOpenDataset: (id: string) => void;
}) {
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const [notice, setNotice] = useState("");
  const [resultRevision, setResultRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let polling = !run || ["Queued", "Running"].includes(run.status);
    let delay = 3000;
    async function load() {
      try {
        const item = await request<PipelineRun>(`/api/pipeline-runs/${encodeURIComponent(runId)}`);
        if (!active) return;
        setRun(item); setError("");
        polling = ["Queued", "Running"].includes(item.status);
        delay = 3000;
        if (!polling) { setNotice(""); setCancelError(""); }
        // 학습 후 Registry 대기를 벗어나면 모델 판본의 외부 등록 상태를 다시 읽는다.
        setResultRevision(revision + (item.current_stage === "training" ? 0 : 1));
      } catch (cause) {
        if (!active) return;
        const temporary = cause instanceof TypeError || (cause instanceof ApiError &&
          (cause.status >= 500 || [408, 429].includes(cause.status)));
        polling = polling && temporary;
        delay = Math.min(delay * 2, 30000);
        setError(formatError(cause) + (polling ? " · 자동 재조회 대기" : ""));
      } finally {
        if (active) {
          setLoading(false);
          if (polling) timer = setTimeout(() => void load(), delay);
        }
      }
    }
    setLoading(true);
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [runId, revision]);
  async function cancel() {
    if (cancelBusy || !run || run.cancel_requested) return;
    setCancelBusy(true); setCancelError("");
    try {
      await request<PipelineRun>(`/api/pipeline-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
      setNotice("취소 요청 접수 · 실제 종료 상태를 확인하는 중입니다.");
    } catch (cause) { setCancelError(formatError(cause)); }
    finally { setCancelBusy(false); setRevision(value => value + 1); }
  }
  const failed = run?.stages?.find(stage => stage.job.status === "Failed");
  return <section aria-label="선택한 파이프라인 실행">
    <div className="panel pipeline-run-header">
      <div className="panel-head"><div><h2>실행 결과</h2>
        {run && <p>{formatRecordedAt(run.created_at)} · <strong>{jobStatusLabel(run.status)}</strong>
          {run.cancel_requested ? " · 취소 요청 중" : ""}</p>}</div>
        <button className="secondary" disabled={loading} onClick={() => setRevision(value => value + 1)}>상태·결과 다시 조회</button>
      </div>
      {loading && <p role="status">실행 상태 조회 중…</p>}
      {error && <p role="alert">실행 상태 조회 실패 · {error}{run ? " · 아래 내용은 마지막 확인 기록입니다." : ""}</p>}
      {notice && <p role="status">{notice}</p>}
      {cancelError && <p role="alert">취소 요청 확인 실패 · {cancelError} · 실행 상태 재조회 필요</p>}
      {run && <>
        <ol className="pipeline-stages" aria-label="단계별 실행 상태">
          {PIPELINE_STAGES.map(([name, label]) => {
            const job = run.stages?.find(stage => stage.name === name)?.job;
            return <li key={name} aria-current={run.current_stage === name ? "step" : undefined}>
              <strong>{label}</strong><span>{job ? jobStatusLabel(job.status) : "실행 기록 없음"}</span>
            </li>;
          })}
        </ol>
        {run.current_stage === "training" && run.stages?.some(stage => stage.name === "training" && stage.job.status === "Succeeded") &&
          <p role="status">학습 완료 · MLflow Registry 등록 확인 대기</p>}
        {(run.status === "Failed" || failed) && <div role="alert" className="notice-blocked">
          <strong>{failed ? `${PIPELINE_STAGES.find(([name]) => name === failed.name)?.[1] ?? failed.name} 단계 실패` : "Workflow 실패 · 단계 실패 지점 미확인"}</strong>
          <p>{failed?.job.error ?? "실행 상태를 다시 조회하고 확인된 단계별 결과를 살펴보세요."}</p>
          <p className="hint">{failed?.job.error_code ?? run.error_code}
            {failed?.job.retry_policy ? ` · 재시도 정책 ${failed.job.retry_policy}` : ""}</p>
        </div>}
        {canCancel && ["Queued", "Running"].includes(run.status) && !run.cancel_requested &&
          <button className="secondary" disabled={cancelBusy || loading || Boolean(error)} onClick={() => void cancel()}>실행 취소 요청</button>}
        <details><summary>실행·단계 식별자</summary>
          <p>실행 {run.id} · 정의 {run.definition_id} v{run.definition_version}</p>
          <p>Dataset {run.dataset_id} · 판본 {run.dataset_version}</p>
          {run.stages?.map(stage => <p key={stage.name}>{stage.name} · Job {stage.job.id}</p>)}
        </details>
      </>}
    </div>
    {run && <PipelineResults run={run} revision={resultRevision} canRun={canRun} canCancel={canCancel} canOpenMLflow={canOpenMLflow}
      onOpenResource={onOpenResource} onOpenDataset={onOpenDataset} />}
  </section>;
}
