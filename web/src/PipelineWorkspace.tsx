import { useEffect, useRef, useState } from "react";
import { ApiError, formatError, request, submitJob } from "./api";
import { PipelineAnalysis } from "./PipelineAnalysis";
import type { PipelineAnalysisSection, PipelineScoreRange } from "./PipelineAnalysis";
import { PipelineCandidateInvestigation } from "./PipelineCandidateInvestigation";
import { ModelLabIntroduction } from "./ModelLabIntroduction";
import { MLTrackingStatus } from "./MLTrackingStatus";
import { formatObservedAt, jobStatusLabel } from "./display-format";
import { dagsterRunsForAnalysis, isLocalOperatorHost, mlflowRegistryUrl } from "./operator-tools";
import type { Dataset, Example, PipelineDefinition as Definition, PipelineCheck as Check,
  PipelineRun as Run, PipelineJob, PipelineCandidates as CandidatePage } from "./types";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

const resultId = (job: PipelineJob | undefined, key: string): string | null => {
  const value = job?.result?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
};

const PIPELINE = "activity-anomaly-if";
const stageLabel: Record<string,string> = {
  preflight: "시간 범위·데이터 검사", training: "모델 학습", calibration: "보정 구간 점수 계산",
  policy: "판정 기준 설정", test: "Test 구간 점수 계산", evaluation: "이상 후보 판정",
};
const DEFAULT_STAGES = Object.keys(stageLabel);
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
const statusLabel: Record<Run["status"], string> = {
  Queued: "대기 중", Running: "진행 중", Succeeded: "완료",
  Failed: "실패", Cancelled: "취소됨",
};
const count = (value: number) => value.toLocaleString("ko-KR");

/** 실패한 SOS 단계만 지목한다. Workflow만 실패한 경우 단계 원인을 추정하지 않는다. */
export function pipelineFailureEvidence(run: Run) {
  const failed = run.stages?.find(stage => stage.job.status === "Failed");
  const code = failed?.job.error_code ?? run.error_code;
  return {
    stage: failed ? stageLabel[failed.name] ?? failed.name
      : code === "SOS-MLFLOW-NOT-CONFIGURED" ? "모델 등록" : null,
    jobId: failed?.job.id,
    code,
    message: failed?.job.error || (code === "SOS-MLFLOW-NOT-CONFIGURED"
      ? "모델 Registry가 구성되지 않아 후속 분석을 진행할 수 없습니다."
      : "실행 실패의 상세 원인을 확인하지 못했습니다. 실행 상태와 운영 로그를 확인하세요."),
    retryPolicy: failed?.job.retry_policy,
    inputIssue: code === "SOS-ML-INPUT-INVALID" || code === "SOS-DATASET-UNREADABLE",
  };
}

export function PipelineFailureNotice({ run, operatorLinks, onInspectInput, onRefresh }: {
  run: Run; operatorLinks: boolean; onInspectInput: () => void; onRefresh: () => void;
}) {
  const evidence = pipelineFailureEvidence(run);
  return <section className="pipeline-readiness-blocked pipeline-failure-notice" role="alert" aria-label="분석 실패와 복구">
    <h3>{evidence.stage ? `실패 지점 · ${evidence.stage}` : "Workflow 실행 실패 · 단계 미확인"}</h3>
    <p>{evidence.message}</p>
    <p>{evidence.retryPolicy === "manual_verification_required"
      ? "실행·커밋 상태 확인이 필요합니다. 운영자가 확인하기 전에는 같은 작업을 다시 제출하지 마세요."
      : evidence.inputIssue ? "입력 조건을 수정한 뒤 새 분석을 시작할 수 있습니다."
      : "현재 실행 상태를 다시 확인하고 오류를 해결한 뒤 새 분석 여부를 판단하세요."}</p>
    <div className="actions">
      <button type="button" className={evidence.inputIssue ? "secondary" : undefined} onClick={onRefresh}>실행 상태 다시 조회</button>
      {evidence.inputIssue && <button type="button" onClick={onInspectInput}>입력 Dataset 확인 →</button>}
      {operatorLinks && <a href={dagsterRunsForAnalysis(run.id)} target="_blank" rel="noopener noreferrer">운영자용 단계 로그 확인 ↗</a>}
    </div>
    <details><summary>오류 코드·실행 근거</summary><p>{evidence.code ?? "오류 코드 미확인"}</p>
      {evidence.jobId && <p>실패한 작업 · {evidence.jobId}</p>}
      <p>분석 · {run.id}</p>
      {operatorLinks && <p>Dagster 재실행은 같은 제출의 단계 Job을 재사용합니다. 새 분석은 SOS에서 별도로 제출합니다.</p>}
    </details>
  </section>;
}

/** Pipeline과 Training Job의 상태가 모두 일치할 때에만 Registry 대기로 본다. */
export function isRegistrationPending(run: Run | null): boolean {
  return run?.status === "Running" && run.current_stage === "training" &&
    (run.stages ?? []).some(stage => stage.name === "training" && stage.job?.status === "Succeeded");
}

type StageState = "done" | "active" | "registering" | "waiting" | "failed" | "cancelled" | "skipped";
const stageStateText: Record<StageState, string> = {
  done: "완료", active: "진행 중", registering: "모델 등록 확인 중", waiting: "대기",
  failed: "실패", cancelled: "취소됨", skipped: "실행 안 됨",
};
const stageMarker: Record<StageState, string> = {
  done: "✓", active: "●", registering: "●", waiting: "○", failed: "✕", cancelled: "–", skipped: "–",
};

/** 실제 단계 Job 상태만 표시한다. 단계 안의 진행률은 측정할 수 없으므로 추정하지 않는다. */
function pipelineStageStates(run: Run, stages: readonly string[]): Array<{ stage: string; state: StageState }> {
  const jobs = new Map((run.stages ?? []).map(item => [item.name, item.job]));
  const registering = isRegistrationPending(run);
  return stages.map(stage => {
    const job = jobs.get(stage);
    if (stage === "training" && registering) return { stage, state: "registering" };
    if (job?.status === "Succeeded") return { stage, state: "done" };
    if (job?.status === "Failed") return { stage, state: "failed" };
    if (job?.status === "Cancelled") return { stage, state: "cancelled" };
    if (job || (run.current_stage === stage && !terminal.has(run.status))) return { stage, state: "active" };
    return { stage, state: terminal.has(run.status) ? "skipped" : "waiting" };
  });
}

function PipelineStageProgress({ run, stages }: { run: Run; stages: readonly string[] }) {
  const states = pipelineStageStates(run, stages);
  const done = states.filter(item => item.state === "done").length;
  return <div className="pipeline-stage-progress">
    <p className="pipeline-stage-count">{stages.length}단계 중 {done}단계 완료</p>
    <ol aria-label="분석 단계 진행 상태">
      {states.map(({ stage, state }) => <li key={stage} className={"pipeline-stage stage-" + state}
        aria-current={state === "active" || state === "registering" ? "step" : undefined}>
        <span className="pipeline-stage-marker" aria-hidden="true">{stageMarker[state]}</span>
        <span className="pipeline-stage-name">{stageLabel[stage] ?? stage}</span>
        <small>{stageStateText[state]}</small>
      </li>)}
    </ol>
  </div>;
}

/** 마지막 히스토그램 구간만 오른쪽 경계를 포함한다. */
export function filterCandidatesByScoreRange(
  candidates: CandidatePage["items"], range: PipelineScoreRange | null,
): CandidatePage["items"] {
  if (!range) return candidates;
  return candidates.filter(item => item.anomaly_score >= range.lower &&
    (item.anomaly_score < range.upper ||
      (range.includeUpper && item.anomaly_score === range.upper)));
}

/** 등록된 분석 방법을 이해·검사·실행하고 자신의 결과로 이동하는 경험. */
export function PipelineWorkspace({ datasets, initialDatasetId, initialRunId, initialCandidateRowIndex, initialAnalysisSection, hasMoreDatasets, onLoadMoreDatasets, onSelectDataset, onSelectRun, onSelectCandidate, onSelectAnalysisSection, onApplyModel, canRun, canTrain = false, canCancel, onNavigate, onOpenResource, canCreateDataset, hasPipelineExample, example, examplesLoading, examplesError, onRetryExamples, onUseExample, canInspectOperations }: {
  datasets: Dataset[]; initialDatasetId: string; initialRunId: string;
  initialCandidateRowIndex?: number;
  initialAnalysisSection?: PipelineAnalysisSection;
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  onSelectDataset: (datasetId: string) => void;
  onSelectRun: (runId: string, datasetId: string) => void;
  onSelectCandidate: (rowIndex: number) => void;
  onSelectAnalysisSection: (section: PipelineAnalysisSection) => void;
  onApplyModel: (modelId: string) => void;
  canRun: boolean; canTrain?: boolean; canCancel: boolean;
  canInspectOperations?: boolean;
  canCreateDataset?: boolean;
  hasPipelineExample?: boolean;
  example?: Example;
  examplesLoading?: boolean;
  examplesError?: string;
  onRetryExamples?: () => void;
  onUseExample?: () => Promise<Dataset>;
  onNavigate: (tab: WorkspaceTab, datasetId: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [localOperatorHost, setLocalOperatorHost] = useState(false);
  useEffect(() => {
    setLocalOperatorHost(isLocalOperatorHost());
  }, []);
  const [catalog, setCatalog] = useState<Definition[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [listReload, setListReload] = useState(0);
  const [datasetId, setDatasetId] = useState(initialDatasetId);
  const selectedDatasetId = useRef(initialDatasetId);
  // 다른 Dataset으로 전환하면 이전 검사·제출의 늦은 응답을 무효화한다.
  const datasetSelectionRevision = useRef(0);
  const [checked, setChecked] = useState<Check | null>(null);
  const [runId, setRunId] = useState(initialRunId);
  const selectedRunId = useRef(runId);
  const pendingRunFocus = useRef<string | null>(null);
  const [loadedRun, setRun] = useState<Run | null>(null);
  // 브라우저 Back/Forward 직후에도 이전 Run을 새 URL의 결과처럼 잠시 보여주지 않는다.
  const run = loadedRun?.id === runId && loadedRun.id === initialRunId ? loadedRun : null;
  const [runLoadError, setRunLoadError] = useState("");
  const [runReload, setRunReload] = useState(0);
  const [rows, setRows] = useState<CandidatePage | null>(null);
  const [candidateError, setCandidateError] = useState("");
  const [candidateLookupError, setCandidateLookupError] = useState("");
  const [candidateLookupStatus, setCandidateLookupStatus] =
    useState<"idle" | "loading" | "missing" | "error">("idle");
  const [candidateLookupRetry, setCandidateLookupRetry] = useState(0);
  // 직접 지정된 한 건은 점수 순위·커서 페이지에 섞지 않는다.
  const [directCandidate, setDirectCandidate] = useState<{
    runId: string; resultId: string; item: CandidatePage["items"][number];
  } | null>(null);
  const [candidateReload, setCandidateReload] = useState(0);
  const [candidateScoreRange, setCandidateScoreRange] = useState<PipelineScoreRange | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<number | null>(initialCandidateRowIndex ?? null);
  const [busy, setBusy] = useState(false);
  const [loadingDatasets, setLoadingDatasets] = useState(false);
  const [error, setError] = useState("");
  const [exampleBusy, setExampleBusy] = useState(false);
  const ownDataDetails = useRef<HTMLDetailsElement>(null);
  const [showOwnData, setShowOwnData] = useState(Boolean(initialDatasetId));
  useEffect(() => { if (initialDatasetId) setShowOwnData(true); }, [initialDatasetId]);
  // React 렌더 전 연속 클릭도 단일 제출로 취급한다.
  const exampleSubmitting = useRef(false);
  const ownSubmitting = useRef(false);
  const [examplePhase, setExamplePhase] = useState<"registering" | "checking" | "submitting" | null>(null);
  useEffect(() => {
    let active = true;
    setCatalogError("");
    void request<Definition[]>("/api/pipelines")
      .then(definitions => {
        if (active) { setCatalog(definitions); setCatalogError(""); }
      })
      .catch(e => { if (active) setCatalogError(formatError(e)); });
    return () => { active = false; };
  }, [listReload]);
  useEffect(() => {
    if (selectedDatasetId.current === initialDatasetId) return;
    selectedDatasetId.current = initialDatasetId;
    datasetSelectionRevision.current += 1;
    setDatasetId(initialDatasetId);
    setChecked(null);
    setBusy(false);
  }, [initialDatasetId]);
  useEffect(() => {
    if (selectedRunId.current !== initialRunId) {
      selectedRunId.current = initialRunId;
      setRunId(initialRunId);
      setRun(null);
      setRows(null);
      setDirectCandidate(null);
      setCandidateLookupStatus("idle");
      setCandidateLookupError("");
      setCandidateScoreRange(null);
      // URL의 Run과 선택 후보는 하나의 탐색 상태다. 이전 Run의 행만 지운 채
      // 이미 URL에 있는 새 Run의 후보 선택을 잃어버리지 않는다.
      setSelectedCandidate(initialCandidateRowIndex ?? null);
      setError("");
      setRunLoadError("");
    }
  }, [initialRunId, initialCandidateRowIndex]);
  useEffect(() => {
    setChecked(null);
  }, [datasetId]);
  useEffect(() => {
    setSelectedCandidate(initialCandidateRowIndex ?? null);
  }, [initialCandidateRowIndex]);
  useEffect(() => {
    // 직접 Run URL은 서버가 반환한 원본 Dataset을 기준으로 연결한다.
    // Dataset을 새로 선택해 URL에서 Run을 지운 경우, 이전 Run으로 되돌리지 않는다.
    if (run && initialRunId && run.id === initialRunId &&
        run.id === runId && datasetId !== run.dataset_id) {
      onSelectRun(run.id, run.dataset_id);
    }
  }, [run?.id, run?.dataset_id, initialRunId, runId, datasetId, onSelectRun]);
  useEffect(() => {
    if (!runId) { setRun(null); setRunLoadError(""); return; }
    let active = true;
    setRunLoadError("");
    let handle: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await request<Run>(`/api/pipeline-runs/${encodeURIComponent(runId)}`);
        if (active && selectedRunId.current === runId) {
          setRun(next);
          setRunLoadError("");
        }
        if (active && selectedRunId.current === runId && !terminal.has(next.status)) {
          handle = setTimeout(() => void refresh(), 2400);
        }
      } catch (e) {
        if (active && selectedRunId.current === runId) {
          setRunLoadError(formatError(e));
          // 권한 거부·존재하지 않는 Run에는 반복 요청하지 않는다.
          const permanent = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429;
          if (!permanent) handle = setTimeout(() => void refresh(), 5000);
        }
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(handle); };
  }, [runId, runReload]);
  useEffect(() => {
    if (run?.status !== "Succeeded") return;
    let active = true;
    setCandidateError("");
    void request<CandidatePage>(`/api/pipeline-runs/${encodeURIComponent(run.id)}/candidates?limit=25`)
      .then(page => {
        if (active && selectedRunId.current === run.id) {
          setRows(page);
          setCandidateError("");
        }
      })
      .catch(e => {
        if (active && selectedRunId.current === run.id) setCandidateError(formatError(e));
      });
    return () => { active = false; };
  }, [run?.id, run?.status, candidateReload]);
  useEffect(() => {
    if (run?.status !== "Succeeded" || !rows || selectedCandidate === null ||
        rows.items.some(item => item.row_index === selectedCandidate) ||
        (directCandidate?.runId === run.id && directCandidate.resultId === rows.result_id &&
          directCandidate.item.row_index === selectedCandidate)) return;
    let active = true;
    setCandidateLookupError("");
    setCandidateLookupStatus("loading");
    const requestedIndex = selectedCandidate;
    const requestedRun = run.id;
    const requestedResult = rows.result_id;
    void request<CandidatePage>(
      `/api/pipeline-runs/${encodeURIComponent(requestedRun)}/candidates?limit=1&row_index=${requestedIndex}`
    ).then(page => {
      if (!active || selectedRunId.current !== requestedRun ||
          page.result_id !== requestedResult) return;
      if (page.items.length === 0) {
        setCandidateLookupStatus("missing");
      } else if (page.items[0].row_index !== requestedIndex) {
        setCandidateLookupStatus("error");
        setCandidateLookupError("조회 결과의 후보 번호가 요청한 값과 일치하지 않습니다.");
      } else {
        setDirectCandidate({ runId: requestedRun, resultId: requestedResult, item: page.items[0] });
        setCandidateLookupStatus("idle");
      }
    }).catch(reason => {
      if (active) {
        setCandidateLookupStatus("error");
        setCandidateLookupError(formatError(reason));
      }
    });
    return () => { active = false; };
  }, [run?.id, run?.status, rows?.result_id, selectedCandidate, candidateLookupRetry]);
  useEffect(() => {
    if (!run || pendingRunFocus.current !== run.id) return;
    pendingRunFocus.current = null;
    moveFocusTo(run.status === "Succeeded" ? "pipeline-analysis-first-heading" : "pipeline-current-run-heading");
  }, [run?.id, run?.status]);

  function moveFocusTo(targetId: string) {
    requestAnimationFrame(() => {
      const target = document.getElementById(targetId);
      target?.scrollIntoView({ block: "start", behavior: "auto" });
      target?.focus({ preventScroll: true });
    });
  }
  function selectCandidateRow(rowIndex: number) {
    setSelectedCandidate(rowIndex);
    setCandidateLookupStatus("idle");
    setCandidateLookupError("");
    onSelectCandidate(rowIndex);
    requestAnimationFrame(() => {
      document.getElementById("pipeline-candidate-detail-heading")?.focus();
    });
  }
  function selectRun(id: string, linkedDatasetId?: string) {
    if (exampleSubmitting.current) return;
    if (id && id === selectedRunId.current && run?.id === id) {
      moveFocusTo(run.status === "Succeeded"
        ? "pipeline-analysis-first-heading" : "pipeline-current-run-heading");
      return;
    }
    const runDatasetId = !id ? "" : linkedDatasetId ??
      (run?.id === id ? run.dataset_id : datasetId);
    if (!id && selectedDatasetId.current) {
      selectedDatasetId.current = "";
      datasetSelectionRevision.current += 1;
      setDatasetId("");
      setChecked(null);
    }
    selectedRunId.current = id;
    pendingRunFocus.current = id || null;
    setRunId(id); setRun(null); setRows(null); setSelectedCandidate(null); setError(""); setBusy(false); setCandidateScoreRange(null);
    setCandidateError("");
    setCandidateLookupError("");
    setCandidateLookupStatus("idle");
    setDirectCandidate(null);
    setRunLoadError("");
    onSelectRun(id, runDatasetId);
    // 선택한 Run의 조회가 완료되면 분석 결과로 이동한다. 먼저 과거 Run으로 이동하지 않는다.
    if (!id) moveFocusTo("pipeline-example-start");
  }
  async function loadMoreDatasets() {
    if (!hasMoreDatasets || loadingDatasets) return;
    setLoadingDatasets(true);
    try { await onLoadMoreDatasets(); }
    catch (e) { setError(formatError(e)); }
    finally { setLoadingDatasets(false); }
  }
  /** 예제 등록·입력 검사·학습 제출을 한 번의 명시적 사용자 행동에 연결한다. */
  async function startExample() {
    if (!catalog.some(item => item.id === PIPELINE) ||
        !onUseExample || !hasPipelineExample || !canCreateDataset || !canTrain ||
        exampleSubmitting.current || ownSubmitting.current || busy) return;
    exampleSubmitting.current = true;
    const previousRevision = datasetSelectionRevision.current;
    const previousRun = selectedRunId.current;
    setExampleBusy(true); setExamplePhase("registering"); setError("");
    try {
      const dataset = await onUseExample();
      if (previousRevision !== datasetSelectionRevision.current ||
          previousRun !== selectedRunId.current) return;
      selectedDatasetId.current = dataset.id;
      const revision = ++datasetSelectionRevision.current;
      setDatasetId(dataset.id); setChecked(null);
      onSelectDataset(dataset.id);
      setExamplePhase("checking");
      const inspection = await request<Check>(
        `/api/pipelines/${PIPELINE}/check?dataset_id=${encodeURIComponent(dataset.id)}`
      );
      if (revision !== datasetSelectionRevision.current ||
          selectedDatasetId.current !== dataset.id ||
          previousRun !== selectedRunId.current) return;
      setChecked(inspection);
      if (!inspection.ready) {
        setShowOwnData(true);
        setError("예제 입력 조건을 통과하지 못했습니다: " + inspection.issues.join(" · "));
        return;
      }
      setExamplePhase("submitting");
      const next = await submitJob<Run>(
        `/api/pipelines/${PIPELINE}/runs`, { dataset_id: dataset.id }
      );
      if (revision === datasetSelectionRevision.current &&
          selectedDatasetId.current === dataset.id &&
          previousRun === selectedRunId.current) {
        exampleSubmitting.current = false;
        selectRun(next.id, dataset.id);
        setRun(next);
      }
    } catch (e) {
      if (previousRun === selectedRunId.current) setError(formatError(e));
    } finally {
      exampleSubmitting.current = false;
      setExampleBusy(false); setExamplePhase(null);
    }
  }
  async function inspect() {
    if (!datasetId || busy || exampleSubmitting.current) return;
    const revision = datasetSelectionRevision.current;
    const requestedDatasetId = datasetId;
    setBusy(true); setError(""); setChecked(null);
    try {
      const inspected = await request<Check>(
        `/api/pipelines/${PIPELINE}/check?dataset_id=${encodeURIComponent(requestedDatasetId)}`
      );
      if (revision === datasetSelectionRevision.current &&
          selectedDatasetId.current === requestedDatasetId) setChecked(inspected);
    } catch (e) {
      if (revision === datasetSelectionRevision.current) setError(formatError(e));
    } finally {
      if (revision === datasetSelectionRevision.current) setBusy(false);
    }
  }
  async function start() {
    if (!datasetId || checked?.dataset_id !== datasetId || !checked.ready ||
        busy || exampleSubmitting.current || ownSubmitting.current || !canTrain) return;
    ownSubmitting.current = true;
    const previousRunId = selectedRunId.current;
    const submittedDatasetId = datasetId;
    const revision = datasetSelectionRevision.current;
    setBusy(true); setError("");
    try {
      const next = await submitJob<Run>(`/api/pipelines/${PIPELINE}/runs`,
        { dataset_id: submittedDatasetId });
      // 제출 중 Run이나 Dataset을 바꿨다면 늦은 결과로 사용자 화면을 전환하지 않는다.
      if (selectedRunId.current === previousRunId &&
          selectedDatasetId.current === submittedDatasetId &&
          datasetSelectionRevision.current === revision) {
        selectRun(next.id);
        setRun(next);
      }
    } catch (e) {
      if (selectedRunId.current === previousRunId &&
          datasetSelectionRevision.current === revision) setError(formatError(e));
    } finally {
      ownSubmitting.current = false;
      if (selectedRunId.current === previousRunId &&
          datasetSelectionRevision.current === revision) setBusy(false);
    }
  }
  async function cancel() {
    if (!run || terminal.has(run.status) || busy || !canCancel) return;
    const requestedRunId = run.id;
    setBusy(true); setError("");
    try {
      const updated = await request<Run>(
        `/api/pipeline-runs/${encodeURIComponent(requestedRunId)}/cancel`,
        { method: "POST" },
      );
      if (selectedRunId.current === requestedRunId) setRun(updated);
    } catch (e) {
      if (selectedRunId.current === requestedRunId) setError(formatError(e));
    } finally {
      if (selectedRunId.current === requestedRunId) setBusy(false);
    }
  }
  async function more() {
    if (!run || !rows?.next_cursor || busy) return;
    const requestedRunId = run.id;
    const resultId = rows.result_id;
    const cursor = rows.next_cursor;
    setBusy(true); setError("");
    try {
      const next = await request<CandidatePage>(
        `/api/pipeline-runs/${encodeURIComponent(requestedRunId)}/candidates?limit=25&cursor=${encodeURIComponent(cursor)}`
      );
      setRows(old => selectedRunId.current === requestedRunId &&
        old?.result_id === resultId && next.result_id === resultId
        ? { ...next, items: [
            ...old.items,
            ...next.items.filter(item => !old.items.some(existing => existing.row_index === item.row_index)),
          ] } : old);
    } catch (e) {
      if (selectedRunId.current === requestedRunId) setError(formatError(e));
    } finally {
      if (selectedRunId.current === requestedRunId) setBusy(false);
    }
  }
  const selected = datasets.find(item => item.id === datasetId);
  const currentCheck = checked?.dataset_id === datasetId ? checked : null;
  const current = catalog.find(item => item.id === PIPELINE);
  const completed = run?.stages ?? [];
  const trainingJob = completed.find(item => item.name === "training")?.job;
  const registrationPending = isRegistrationPending(run);
  const shownCandidates = filterCandidatesByScoreRange(rows?.items ?? [], candidateScoreRange);
  const selectedInPage = rows?.items.find(item => item.row_index === selectedCandidate);
  const selectedFromUrl = directCandidate?.runId === run?.id &&
    directCandidate?.resultId === rows?.result_id &&
    directCandidate?.item.row_index === selectedCandidate
    ? directCandidate.item : null;
  const fetchedSelected = selectedInPage ?? selectedFromUrl;
  const activeCandidate = fetchedSelected &&
    filterCandidatesByScoreRange([fetchedSelected], candidateScoreRange).length
    ? fetchedSelected : null;
  const activeDataset = run ? datasets.find(item => item.id === run.dataset_id) : null;
  const modelRevisionId = resultId(completed.find(item => item.name === "training")?.job, "model_revision_id");
  const testScoringId = resultId(completed.find(item => item.name === "test")?.job, "scoring_result_id");
  const evaluationId = resultId(completed.find(item => item.name === "evaluation")?.job, "evaluation_revision_id");
  const inputReady = Boolean(currentCheck?.ready);
  const activeStage = run?.current_stage ? stageLabel[run.current_stage] ?? run.current_stage : null;
  const hasActiveAnalysis = Boolean(runId);
  return <div className="platform-home pipeline-consumer">
    {!hasActiveAnalysis && <ModelLabIntroduction definition={current ?? null}
      example={example} examplesLoading={Boolean(examplesLoading)}
      examplesError={examplesError ?? ""} onRetryExamples={onRetryExamples}
      catalogError={catalogError} onRetryCatalog={() => setListReload(n => n + 1)}
      exampleBusy={exampleBusy || busy} examplePhase={examplePhase}
      canCreateDataset={Boolean(canCreateDataset)} canRun={canTrain}
      onStartExample={() => void startExample()}
      onOwnData={() => {
        if (ownDataDetails.current) {
          setShowOwnData(true);
          requestAnimationFrame(() => ownDataDetails.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
        }
      }} />}
    {error && <div className="alert error" role="alert">
      {error.includes("SOS-API-DEPENDENCY") ? <>
        <strong>Workflow 실행 서버에서 요청을 확인하지 못했습니다.</strong>
        <p>Dataset 구조 검사와는 별개의 오류입니다. 이미 실행이 접수됐을 수 있으니
          분석 이력과 Dagster Run 상태를 확인한 뒤 다시 제출하세요.</p>
        <details><summary>오류 진단 정보</summary>{error}</details>
        <button type="button" className="secondary"
          onClick={() => onNavigate("pipeline-jobs", datasetId)}>분석 이력 확인 →</button>
      </> : error}
      <button className="ghost" onClick={() => setError("")}>닫기</button>
    </div>}
    {!hasActiveAnalysis && <details ref={ownDataDetails} className="pipeline-advanced"
      open={showOwnData}
      onToggle={event => setShowOwnData(event.currentTarget.open)}>
      <summary>내 Dataset으로 모델 학습 준비</summary>
    <section className="panel pipeline-prepare" aria-labelledby="pipeline-prepare-title">
      <div className="pipeline-section-heading">
        <div><span className="eyebrow">내 Dataset으로 실행</span>
          <h3 id="pipeline-prepare-title" tabIndex={-1}>학습할 Dataset 선택</h3>
          <p>이 Pipeline은 등록된 {current?.required_features.length ?? 44}개 활동 Feature와
            <code> ts</code> 시간 컬럼을 고정 입력으로 사용합니다.
            Dataset에 추가 컬럼이 있어도 되며, Feature를 직접 다시 선택할 필요는 없습니다.</p>
        </div>
        <span className="chip">{!datasetId ? "Dataset 선택 전" :
          currentCheck?.ready ? "입력 구조 확인됨" :
          currentCheck ? "입력 조건 확인 필요" : "검사 전"}</span>
      </div>
      <details className="pipeline-evidence">
        <summary>지원하는 입력 데이터 조건</summary>
        <p>시간 컬럼 <code>ts</code>와 {current?.required_features.length ?? 44}개
          수치형 활동 Feature가 준비된 Dataset이 필요합니다.</p>
        <p>원시 로그를 자동으로 활동 Feature로 변환하는 파이프라인은 포함되지 않습니다.</p>
        {current && <details>
          <summary>필수 Feature와 실행 제약 확인</summary>
          <p className="pipeline-feature-list">{current.required_features.join(" · ")}</p>
          <ul>{current.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>
        </details>}
      </details>
      <div className="form-row">
        <label>분석할 Dataset
          <select aria-label="Pipeline 분석 Dataset" value={datasetId} disabled={exampleBusy} onChange={e => {
            const nextId = e.target.value;
            if (selectedDatasetId.current === nextId) return;
            selectedDatasetId.current = nextId;
            datasetSelectionRevision.current += 1;
            setDatasetId(nextId);
            setChecked(null);
            setBusy(false);
            setError("");
            onSelectDataset(nextId);
          }}>
            <option value="">내 Dataset 선택</option>
            {datasetId && !selected &&
              <option value={datasetId}>현재 목록 외 Dataset · {datasetId}</option>}
            {datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        {hasMoreDatasets && <button className="secondary" disabled={loadingDatasets}
          onClick={() => void loadMoreDatasets()}>
          {loadingDatasets ? "목록 불러오는 중…" : "이전 Dataset 더 보기"}
        </button>}
      </div>
      {datasetId && <p className="hint">선택한 Dataset: {selected?.name ?? datasetId} ·
        판본: {selected?.version.slice(0, 16) ?? "현재 목록에서 판본 조회 불가"}</p>}
      <div className="pipeline-actions">
        <button className="secondary" type="button" disabled={exampleBusy} onClick={() => onNavigate("understanding", datasetId)}>
          Dataset 등록·내용 확인 →
        </button>
        <button className="secondary" type="button" disabled={exampleBusy} onClick={() => onNavigate("preparation", datasetId)}>
          독립 데이터 준비 도구 →
        </button>
        <button type="button" disabled={!current || !datasetId || busy || exampleBusy}
          onClick={() => void inspect()}>
          {busy ? "확인 중…" : "이 Dataset의 입력 조건 확인"}
        </button>
      </div>
      {!datasetId && <div className="pipeline-next-step" role="status">
        <strong>다음 행동: Dataset을 선택하세요.</strong>
        <p>등록된 Dataset이 없다면 데이터 등록·내용 확인으로 이동할 수 있습니다.</p>
      </div>}
      {datasetId && !currentCheck && <p className="pipeline-next-step" role="status">
        Dataset을 선택했습니다. <strong>입력 조건 확인</strong>을 누르면 필요한 컬럼과 자료형을 검사합니다.
        이 확인은 분석 Job을 자동 실행하지 않습니다.
      </p>}
      {currentCheck && !currentCheck.ready && <div role="alert" className="pipeline-readiness-blocked">
        <strong>현재 Dataset은 이 분석의 입력 계약과 맞지 않습니다.</strong>
        <p>다음 내용을 확인한 뒤 데이터를 준비하거나 다른 Dataset을 선택하세요.</p>
        <ul>{currentCheck.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
        <div className="pipeline-actions">
          <button type="button" className="secondary" onClick={() => onNavigate("understanding", datasetId)}>
            Dataset 구조 확인 →
          </button>
          <button type="button" className="secondary" onClick={() => onNavigate("preparation", datasetId)}>
            가능한 변환 확인 →
          </button>
        </div>
        <p className="hint">원본 로그의 Feature 생성 규칙이 등록되지 않았다면,
          이 화면에서 자동으로 준비할 수는 없습니다.</p>
      </div>}
      {inputReady && <div className="pipeline-readiness-ready" role="status">
        <strong>입력 구조 확인 완료</strong>
        <p>필수 Feature와 시간 컬럼의 구조가 일치합니다. 전체 시간 값과
          Train·Calibration·Test의 실제 분할 가능성은 실행 Worker가 검사합니다.</p>
        <button type="button" disabled={!canTrain || busy || exampleBusy} onClick={() => void start()}>
          {busy ? "제출 중…" : "이 데이터로 분석 실행 →"}
        </button>
        {!canTrain && <p>현재 계정에는 모델 학습 실행 권한이 없습니다.</p>}
      </div>}
    </section>
    </details>}

    {hasActiveAnalysis && run && run.status !== "Succeeded" && <section className="panel pipeline-run-context" aria-labelledby="pipeline-current-run-heading">
      <div className="pipeline-run-context-header">
        <div>
          <span className="eyebrow">학습 · 결과 탐구</span>
          <h3 id="pipeline-current-run-heading" tabIndex={-1}>{
            run ? "모델 학습 · " + statusLabel[run.status] : "학습 기록을 불러오는 중"
          }</h3>
        </div>
        {canTrain && <button type="button" className="secondary" disabled={exampleBusy}
          onClick={() => selectRun("")}>새 모델 학습 →</button>}
      </div>
        {run.status === "Failed" && <PipelineFailureNotice run={run}
          operatorLinks={Boolean(canInspectOperations && localOperatorHost)}
          onInspectInput={() => onNavigate("understanding", run.dataset_id)}
          onRefresh={() => setRunReload(value => value + 1)} />}
      {run && <div className="pipeline-context-facts">
        <div><span>실행 상태</span><strong>{
          runLoadError ? "마지막 확인: " + statusLabel[run.status] : statusLabel[run.status]
        }{run.cancel_requested ? " · 취소 요청됨" : ""}</strong></div>
        <div><span>Dataset</span><strong>{activeDataset?.name ?? run.dataset_id}</strong></div>
        <details className="pipeline-context-technical">
          <summary>판본·기술 식별자</summary>
          <dl>
            <div><dt>Dataset 판본</dt><dd><code title={run.dataset_version}>{run.dataset_version}</code></dd></div>
            <div><dt>실행 ID</dt><dd><code title={run.id}>{run.id}</code></dd></div>
          </dl>
        </details>
      </div>}
      {run.status === "Failed" ? <details className="pipeline-failed-stages">
        <summary>단계별 실행 상태</summary>
        <PipelineStageProgress run={run} stages={current?.stages ?? DEFAULT_STAGES} />
      </details> : <PipelineStageProgress run={run} stages={current?.stages ?? DEFAULT_STAGES} />}
      {run && runLoadError && <div className="pipeline-context-error" role="alert">
        <p>실행 상태를 다시 확인하지 못했습니다: {runLoadError}</p>
        <button type="button" className="secondary"
          onClick={() => setRunReload(value => value + 1)}>실행 상태 다시 조회</button>
      </div>}
    </section>}
    {run?.status === "Succeeded" && <section className="panel pipeline-results-priority"
      aria-labelledby="pipeline-analysis-first-heading">
      <div className="pipeline-section-heading">
        <div>
          <h3 id="pipeline-analysis-first-heading" tabIndex={-1}>학습 결과</h3>
        </div>
        <div className="pipeline-results-actions">
          {modelRevisionId && <button type="button" className="secondary"
            onClick={() => onApplyModel(modelRevisionId)}>이 모델로 다른 데이터 분석 →</button>}
          {canTrain && <button type="button" className="secondary" disabled={exampleBusy}
            onClick={() => selectRun("")}>새 모델 학습</button>}
        </div>
      </div>
      {runLoadError && <div className="pipeline-readiness-blocked" role="alert">
        <strong>실행 상태를 마지막으로 갱신하지 못했습니다.</strong>
        <p>{runLoadError}</p>
        <button type="button" className="secondary"
          onClick={() => setRunReload(value => value + 1)}>실행 상태 다시 확인</button>
      </div>}
      <PipelineAnalysis run={run} onOpenResource={onOpenResource}
        initialSection={initialAnalysisSection} onSectionChange={onSelectAnalysisSection}
        onInvestigateCandidates={() => moveFocusTo("pipeline-candidates-heading")}
        onSelectTestScoreRange={range => {
          setCandidateScoreRange(range);
          moveFocusTo("pipeline-candidates-heading");
        }} />
    </section>}
    {hasActiveAnalysis && <section className="panel pipeline-results"
      aria-label={run?.status === "Succeeded" ? "이상 후보 탐구 및 실행 근거"
        : run ? "학습 진행 및 실행 근거" : undefined}
      aria-labelledby={!run ? "pipeline-run-heading" : undefined}>
      {!run && <div className="pipeline-section-heading">
        <div><span className="eyebrow">학습 기록</span>
          <h3 id="pipeline-run-heading">학습 결과를 불러오는 중</h3>
        </div>
      </div>}
      {!run && <div className="pipeline-next-step" role={runLoadError ? "alert" : "status"}>
        <strong>{runId ? runLoadError ? "선택한 실행을 조회하지 못했습니다." :
          "선택한 실행을 불러오는 중입니다." : "아직 선택된 실행이 없습니다."}</strong>
        {runId && runLoadError && <>
          <p>{runLoadError}</p>
          <div className="pipeline-actions">
            <button type="button" className="secondary" onClick={() =>
              setRunReload(n => n + 1)}>이 분석 다시 조회</button>
            {canTrain && <button type="button" className="secondary" disabled={exampleBusy} onClick={() => selectRun("")}>
              새 분석 준비 →
            </button>}
          </div>
        </>}
        {canTrain && runId && !runLoadError && <button type="button" className="secondary"
          disabled={exampleBusy} onClick={() => selectRun("")}>
          새 모델 학습 →
        </button>}
        {!runId && <p>위에서 Dataset을 선택하고 입력 구조를 확인한 뒤 명시적으로 실행하거나,
          아래의 이전 학습 결과를 열어 보세요.</p>}
      </div>}
      {run && <>
        {run.status === "Cancelled" && <p className="pipeline-next-step" role="status">
          실행 취소가 확정되었습니다. 기존 기록은 보존되며 필요하다면 새 Run을 실행할 수 있습니다.
          {(run.stages ?? []).some(stage => stage.name === "training" && stage.job?.status === "Succeeded") &&
            " 이미 완료한 학습의 MLflow 등록 요청과 자동 재시도는 별도로 계속될 수 있습니다."}
        </p>}
        {!terminal.has(run.status) && <div className="pipeline-next-step" role="status">
          <strong>{run.cancel_requested ? "취소 요청을 처리하고 있습니다." :
            run.status === "Queued" ? "분석 실행을 기다리고 있습니다." :
            registrationPending ? "모델 학습 완료 · 등록 결과 대기 중" :
            activeStage ? activeStage + " 단계 진행 중" : "실행 대기 또는 단계 전환 중"}</strong>
          {registrationPending ? <>
            <p>학습은 완료됐습니다. 결과 등록 확인 후 다른 시간 구간을 분석합니다.</p>
            {trainingJob && <MLTrackingStatus key={trainingJob.id} jobId={trainingJob.id}
              operatorLinks={canInspectOperations && localOperatorHost} />}
            {canInspectOperations && localOperatorHost && <details className="pipeline-operator-links">
              <summary>운영자용 Run·Registry 상세 확인</summary>
              <p>Dagster에서 이 분석의 Run과 단계 로그를 확인하고,
                MLflow의 Run ID 및 Registered Model Version과 대조할 수 있습니다.</p>
              <div className="actions">
                <a href={dagsterRunsForAnalysis(run.id)} target="_blank" rel="noopener noreferrer">
                  운영자용 단계 로그 확인 ↗
                </a>
                <a href={mlflowRegistryUrl()} target="_blank" rel="noopener noreferrer">
                  MLflow 모델 Registry 열기 ↗
                </a>
              </div>
              <p className="hint">별도 인증이 없는 로컬 운영 UI입니다. SOS의 작업 상태와 권한을 대신하지 않습니다.</p>
            </details>}
          </> : null}
          {canCancel && <div className="pipeline-cancel-action">
            {registrationPending && <p className="hint">
              실행 취소는 남은 단계의 중단 요청입니다.
              이미 완료된 학습 결과와 별도로 접수된 MLflow 등록 재시도는 삭제하거나 취소하지 않습니다.
            </p>}
            <button type="button" className="secondary"
              disabled={busy || run.cancel_requested} onClick={() => void cancel()}>
              학습 실행 취소
            </button>
          </div>}
        </div>}
        {run.status === "Succeeded" && <section className="pipeline-candidates" aria-labelledby="pipeline-candidates-heading">
          <h4 id="pipeline-candidates-heading" tabIndex={-1}>검토할 이상 후보</h4>
          <p>Test 데이터에서 점수가 판정 기준 이상인 관측치입니다. 하나를 선택하면 실제 Feature 값을 확인할 수 있습니다.</p>
          <div className="pipeline-actions">
            <button type="button" className="secondary" onClick={() =>
              onNavigate("understanding", run.dataset_id)}>분석 데이터 보기 →</button>
          </div>
          {candidateScoreRange && <div className="pipeline-next-step" role="status">
            <strong>선택한 점수 범위: {candidateScoreRange.lower.toPrecision(4)} ~ {candidateScoreRange.upper.toPrecision(4)}</strong>
            <p>현재 불러온 후보만 이 범위로 좁혀 보여줍니다. 실제 판정 기준이나 전체 후보 수는 바뀌지 않습니다.</p>
            <button type="button" className="secondary" onClick={() => setCandidateScoreRange(null)}>
              점수 필터 해제
            </button>
          </div>}
          {!rows && candidateError && <div className="pipeline-readiness-blocked" role="alert">
            <strong>후보 조회에 실패했습니다.</strong>
            <p>{candidateError}</p>
            <button type="button" className="secondary" onClick={() => {
              setCandidateError("");
              setCandidateReload(current => current + 1);
            }}>후보 다시 조회</button>
          </div>}
          {!rows && !candidateError && <p role="status">
            완료된 Run의 후보를 불러오는 중입니다.
          </p>}
          {rows && <>
            {rows.items.length === 0 && <p>현재 조회에서 이상 후보가 없습니다.</p>}
            {rows.items.length > 0 && (rows.next_cursor || candidateScoreRange) &&
              <p className="pipeline-result-count">
                점수순으로 {count(rows.items.length)}건을 불러왔습니다{rows.next_cursor ? " (후보가 더 있습니다)" : ""}
                {candidateScoreRange ? ` · 점수 범위에 해당하는 ${count(shownCandidates.length)}건 표시` : ""}
              </p>}
            {rows.items.length > 0 && shownCandidates.length === 0 &&
              <p className="hint">현재 불러온 후보에서 이 점수 구간과 일치하는 항목이 없습니다.
                필터를 해제하거나 다음 후보를 불러와 확인하세요.</p>}
            {(rows.items.length > 0 || selectedCandidate !== null) && <div className="pipeline-candidate-workspace">
              <div className="pipeline-candidate-list">
                {shownCandidates.length > 0 && <div className="pipeline-candidate-top-panel">
                  <div className="pipeline-candidate-top-heading">
                    <strong>{candidateScoreRange ? "선택한 점수 범위의 상위 후보" : "점수가 높은 후보"}</strong>
                  </div>
                  <div className="pipeline-candidate-top-list" aria-label="조회한 이상 후보 목록">
                    {shownCandidates.slice(0, 3).map((item, i) => <button
                      key={item.row_index} type="button" className="ghost pipeline-candidate-top-item"
                      aria-pressed={selectedCandidate === item.row_index}
                      onClick={() => selectCandidateRow(item.row_index)}>
                      <span className="pipeline-candidate-top-rank">{i+1}위</span>
                      <span className="pipeline-candidate-top-meta">
                        <strong title={item.observed_at ?? undefined}>{item.observed_at
                          ? formatObservedAt(item.observed_at) : "결과 행 " + count(item.row_index)}</strong>
                        {item.observed_at && <small>결과 행 {count(item.row_index)}</small>}
                      </span>
                      <span className="pipeline-candidate-top-score">
                        <small>이상 점수</small>
                        <strong>{item.anomaly_score.toPrecision(6)}</strong>
                      </span>
                    </button>)}
                  </div>
                </div>}
                <details className="pipeline-candidate-table">
                  <summary>조회한 후보 모두 보기 · {count(shownCandidates.length)}건</summary>
                <div className="table-scroll"><table>
              <thead><tr><th>결과 행 번호</th><th>이상 점수</th><th>선택·확인</th></tr></thead>
              <tbody>{shownCandidates.map(item => <tr key={item.row_index}
                className={selectedCandidate === item.row_index ? "pipeline-candidate-selected" : ""}>
                <td>{count(item.row_index)}</td>
                <td>{item.anomaly_score.toPrecision(6)}</td>
                <td><button type="button" className="secondary" aria-pressed={selectedCandidate === item.row_index}
                  onClick={() => selectCandidateRow(item.row_index)}>
                  {selectedCandidate === item.row_index ? "선택됨" : "이 후보 분석"}
                </button></td>
              </tr>)}</tbody>
                </table></div>
                </details>
                {rows.next_cursor && <button type="button" className="secondary"
                  disabled={busy} onClick={() => void more()}>다음 후보 25건 불러오기</button>}
              </div>
              <div className="pipeline-candidate-detail">
                {selectedCandidate !== null && !activeCandidate && <div className="pipeline-next-step" role="status">
                  {candidateScoreRange && fetchedSelected
                    ? <>
                      <strong>선택한 후보가 현재 점수 필터 밖에 있습니다.</strong>
                      <p>필터를 해제하면 선택했던 후보를 다시 확인할 수 있습니다.</p>
                      <button type="button" className="secondary" onClick={() =>
                        setCandidateScoreRange(null)}>점수 필터 해제</button>
                    </>
                    : candidateLookupStatus === "missing" ? <>
                      <strong>선택한 후보를 찾지 못했습니다.</strong>
                      <p>이 결과 행 번호는 현재 분석의 이상 후보에 포함되지 않습니다.
                        다른 후보를 선택하거나 결과 주소를 확인하세요.</p>
                    </> : candidateLookupStatus === "error" ? <>
                      <strong>선택한 후보를 조회하지 못했습니다.</strong>
                      <p role="alert">{candidateLookupError}</p>
                      <button type="button" className="secondary" onClick={() =>
                        setCandidateLookupRetry(n => n + 1)}>이 후보 다시 조회</button>
                    </> : <>
                      <strong>선택했던 후보 #{count(selectedCandidate)}를 불러오는 중입니다.</strong>
                      <p>해당 후보의 관측 근거를 직접 조회합니다.</p>
                    </>}
                </div>}
                {activeCandidate && selectedFromUrl && !selectedInPage && <p className="hint">
                  결과 주소에서 직접 연 후보입니다. 조회한 점수순 목록은 별도로 유지됩니다.
                </p>}
                {activeCandidate ? <PipelineCandidateInvestigation
                  key={run.id + ":" + rows.result_id + ":" + activeCandidate.row_index}
                  candidate={activeCandidate} scoringResultId={rows.result_id}
                  modelRevisionId={modelRevisionId}
                  canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource} />
                  : selectedCandidate === null ? <div className="pipeline-next-step" role="status">
                    <strong>위 목록에서 후보를 선택하세요.</strong>
                  </div> : null}
              </div>
            </div>}
          </>}
        </section>}
        <details className="pipeline-evidence">
          <summary>단계별 실행 상태·분할 범위·기술 근거</summary>
          {canInspectOperations && localOperatorHost && <p className="hint">
            운영자 확인: <a href={dagsterRunsForAnalysis(run.id)} target="_blank"
              rel="noopener noreferrer">운영자용 단계 로그 확인 ↗</a>
            {" "}(태그 sos/submission_id = {run.id})
          </p>}
          {canInspectOperations && localOperatorHost && <p className="hint">Dagster 재실행은 같은 제출의 단계 Job을 재사용합니다. 새 실험은 SOS에서 새 분석으로 제출합니다.</p>}
          {run.input_plan && <div className="steps">
            <strong>Worker가 확정한 실제 시간 분할</strong>
            <p>Train 종료: {new Date(run.input_plan.train_end).toLocaleString("ko-KR")} ·
              Calibration 종료: {new Date(run.input_plan.calibration_end).toLocaleString("ko-KR")}</p>
            <p>Train {count(run.input_plan.partition_sizes.train)}행 ·
              Calibration {count(run.input_plan.partition_sizes.calibration)}행 ·
              Test {count(run.input_plan.partition_sizes.test)}행</p>
          </div>}
          <div className="job-list">
            {current?.stages.map(stage => {
              const entry = completed.find(item => item.name === stage);
              const modelId = resultId(entry?.job, "model_revision_id");
              const scoringId = resultId(entry?.job, "scoring_result_id");
              const evaluation = resultId(entry?.job, "evaluation_revision_id");
              return <div className="ml-resource-row" key={stage}>
                <strong>{stageLabel[stage] ?? stage}</strong>
                <span>{entry ? jobStatusLabel(entry.job.status) : run.current_stage === stage ? "대기" : "미시작"}</span>
                {entry?.job.error_code && <small>오류 {entry.job.error_code}</small>}
                {modelId && <button type="button" className="secondary"
                  onClick={() => onOpenResource("model", modelId)}>모델 결과 →</button>}
                {scoringId && <button type="button" className="secondary"
                  onClick={() => onOpenResource("scoring", scoringId)}>점수 결과 →</button>}
                {evaluation && <button type="button" className="secondary"
                  onClick={() => onOpenResource("evaluation", evaluation)}>평가 결과 →</button>}
              </div>;
            })}
          </div>
        </details>
      </>}
    </section>}
  </div>;
}
