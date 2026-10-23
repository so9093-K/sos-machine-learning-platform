import { useEffect, useRef, useState } from "react";
import { ApiError, formatError, request, submitJob } from "./api";
import { PipelineAnalysis } from "./PipelineAnalysis";
import type { PipelineAnalysisSection } from "./PipelineAnalysis";
import { PipelineCandidateInvestigation } from "./PipelineCandidateInvestigation";
import type { Dataset, PipelineDefinition as Definition, PipelineCheck as Check,
  PipelineRun as Run, PipelineJob, PipelineCandidates as CandidatePage } from "./types";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

const resultId = (job: PipelineJob | undefined, key: string): string | null => {
  const value = job?.result?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
};

const PIPELINE = "activity-anomaly-if";
const stageLabel: Record<string,string> = {
  preflight: "시간 범위·데이터 검사", training: "모델 학습", calibration: "Calibration 점수화",
  policy: "판정 기준 생성", test: "Test 점수화", evaluation: "정책 적용 평가",
};
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
const count = (value: number) => value.toLocaleString("ko-KR");

/** 등록된 분석 방법을 이해·검사·실행하고 자신의 결과로 이동하는 경험. */
export function PipelineWorkspace({ datasets, initialDatasetId, initialRunId, initialCandidateRowIndex, initialAnalysisSection, hasMoreDatasets, onLoadMoreDatasets, onSelectDataset, onSelectRun, onSelectCandidate, onSelectAnalysisSection, canRun, canCancel, onNavigate, onOpenResource }: {
  datasets: Dataset[]; initialDatasetId: string; initialRunId: string;
  initialCandidateRowIndex?: number;
  initialAnalysisSection?: PipelineAnalysisSection;
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  onSelectDataset: (datasetId: string) => void;
  onSelectRun: (runId: string, datasetId: string) => void;
  onSelectCandidate: (rowIndex: number) => void;
  onSelectAnalysisSection: (section: PipelineAnalysisSection) => void;
  canRun: boolean; canCancel: boolean;
  onNavigate: (tab: WorkspaceTab, datasetId: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [catalog, setCatalog] = useState<Definition[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [historyError, setHistoryError] = useState("");
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
  const [history, setHistory] = useState<Run[]>([]);
  const [rows, setRows] = useState<CandidatePage | null>(null);
  const [candidateError, setCandidateError] = useState("");
  const [candidateReload, setCandidateReload] = useState(0);
  const [selectedCandidate, setSelectedCandidate] = useState<number | null>(initialCandidateRowIndex ?? null);
  const [busy, setBusy] = useState(false);
  const [loadingDatasets, setLoadingDatasets] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    // Run 이력이 일시적으로 실패해도 Pipeline Catalog 자체는 이용할 수 있다.
    setCatalogError(""); setHistoryError("");
    void request<Definition[]>("/api/pipelines")
      .then(definitions => {
        if (active) { setCatalog(definitions); setCatalogError(""); }
      })
      .catch(e => { if (active) setCatalogError(formatError(e)); });
    void request<Run[]>("/api/pipeline-runs")
      .then(runs => {
        if (active) { setHistory(runs); setHistoryError(""); }
      })
      .catch(e => { if (active) setHistoryError(formatError(e)); });
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
    onSelectCandidate(rowIndex);
    requestAnimationFrame(() => {
      document.getElementById("pipeline-candidate-detail-heading")?.focus();
    });
  }
  function selectRun(id: string) {
    const runDatasetId = history.find(item => item.id === id)?.dataset_id ??
      (run?.id === id ? run.dataset_id : datasetId);
    selectedRunId.current = id;
    pendingRunFocus.current = id || null;
    setRunId(id); setRun(null); setRows(null); setSelectedCandidate(null); setError(""); setBusy(false);
    setCandidateError("");
    setRunLoadError("");
    onSelectRun(id, runDatasetId);
    // 선택한 Run의 조회가 완료되면 분석 결과로 이동한다. 먼저 과거 Run으로 이동하지 않는다.
    if (!id) moveFocusTo("pipeline-prepare-title");
  }
  async function loadMoreDatasets() {
    if (!hasMoreDatasets || loadingDatasets) return;
    setLoadingDatasets(true);
    try { await onLoadMoreDatasets(); }
    catch (e) { setError(formatError(e)); }
    finally { setLoadingDatasets(false); }
  }
  async function inspect() {
    if (!datasetId || busy) return;
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
    if (!datasetId || checked?.dataset_id !== datasetId || !checked.ready || busy || !canRun) return;
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
      void request<Run[]>("/api/pipeline-runs").then(setHistory).catch(() => {});
    } catch (e) {
      if (selectedRunId.current === previousRunId &&
          datasetSelectionRevision.current === revision) setError(formatError(e));
    } finally {
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
        ? { ...next, items: [...old.items, ...next.items] } : old);
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
  const activeCandidate = rows?.items.find(item => item.row_index === selectedCandidate);
  const activeDataset = run ? datasets.find(item => item.id === run.dataset_id) : null;
  const modelRevisionId = resultId(completed.find(item => item.name === "training")?.job, "model_revision_id");
  const testScoringId = resultId(completed.find(item => item.name === "test")?.job, "scoring_result_id");
  const evaluationId = resultId(completed.find(item => item.name === "evaluation")?.job, "evaluation_revision_id");
  const inputReady = Boolean(currentCheck?.ready);
  const activeStage = run?.current_stage ? stageLabel[run.current_stage] ?? run.current_stage : null;
  const currentJourneyStep = run ? run.status === "Succeeded" ? 4 : 3 :
    inputReady ? 3 : datasetId ? 2 : 1;
  return <div className="platform-home pipeline-consumer">
    <section className="platform-intro pipeline-hero">
      <span className="eyebrow">PIPELINE EXPERIENCE · 설계된 분석 이용</span>
      <h2>시간 기반 활동 이상 탐지</h2>
      <p>등록된 방법으로 내 활동 데이터를 분석해 이상 점수와 검토 후보를 확인합니다.
        모델과 단계별 설정은 이미 설계돼 있으므로, 사용할 Dataset과 실행 여부에 집중하세요.</p>
      <div className="pipeline-hero-foot">
        <span>현재 제공: 준비된 Feature Dataset을 이용하는 분석</span>
        <span>결과: 이상 점수 · 후보 목록 · 실행 근거</span>
      </div>
      <div className="pipeline-actions">
        <button type="button" onClick={() =>
          document.getElementById(run?.status === "Succeeded"
            ? "pipeline-analysis-first-heading" : "pipeline-prepare-title")?.scrollIntoView({ block: "start" })}>
          {run?.status === "Succeeded" ? "이번 Run의 분석 결과 보기 →" : "내 데이터로 분석 시작하기 →"}
        </button>
      </div>
    </section>

    {(Boolean(!runId)) && <div className="pipeline-journey" aria-label="분석 이용 단계" role="group">
      {[
        { name: "분석 이해", detail: "목적·입력·결과" },
        { name: "데이터 준비", detail: "등록·선택·확인" },
        { name: "실행", detail: "상태·취소·복구" },
        { name: "결과 활용", detail: "후보·상세 근거" },
      ].map((step, index) => <div key={step.name}
        className={"pipeline-journey-item" + (currentJourneyStep === index + 1 ? " active" : "")}
        aria-current={currentJourneyStep === index + 1 ? "step" : undefined}>
        <span>{String(index + 1).padStart(2, "0")}</span>
        <strong>{step.name}</strong>
        <small>{step.detail}</small>
      </div>)}
    </div>}
    <section className="panel pipeline-run-context" aria-labelledby="pipeline-current-run-heading">
      <div className="pipeline-run-context-header">
        <div>
          <span className="eyebrow">분석 작업 공간 · Run 선택</span>
          <h3 id="pipeline-current-run-heading" tabIndex={-1}>{
            run ? "선택한 분석 Run" : runId ? "선택한 Run을 확인하는 중" : "새 분석 또는 이전 Run"
          }</h3>
          <p>{run ? "실행에 고정된 Dataset 판본과 상태를 확인합니다." :
            "이전에 수행한 분석을 열거나 새로운 분석을 실행하세요."}</p>
          {historyError && <p className="pipeline-context-error" role="alert">
            이전 Run 목록 조회 실패: {historyError}
            <button type="button" className="secondary" onClick={() =>
              setListReload(n => n + 1)}>목록 다시 조회</button>
          </p>}
        </div>
        <label>분석 Run 선택
          <select aria-label="분석 Run 선택" value={runId} onChange={event => selectRun(event.target.value)}>
            <option value="">새 분석 시작</option>
            {runId && !history.some(item => item.id === runId) &&
              <option value={runId}>현재 Run · {runId.slice(0, 16)}</option>}
            {history.map(item => <option key={item.id} value={item.id}>{
              datasets.find(dataset => dataset.id === item.dataset_id)?.name ??
                item.dataset_id.slice(0, 14)
            } · {item.status} · {new Date(item.created_at).toLocaleDateString("ko-KR")}</option>)}
          </select>
        </label>
      </div>
      {run && <div className="pipeline-context-facts">
        <div><span>실행 상태</span><strong>{
          runLoadError ? "마지막 확인: " + run.status : run.status
        }{run.cancel_requested ? " · 취소 요청됨" : ""}</strong></div>
        <div><span>Dataset</span><strong>{activeDataset?.name ?? run.dataset_id}</strong></div>
        <div><span>Dataset 판본</span><code title={run.dataset_version}>{run.dataset_version}</code></div>
        <div><span>Run</span><code title={run.id}>{run.id}</code></div>
      </div>}
      {runId && runLoadError && <p className="pipeline-context-error" role="alert">
        마지막 Run 조회 실패: {runLoadError} · 아래에서 다시 조회할 수 있습니다.
      </p>}
    </section>
    {run?.status === "Succeeded" && <section className="panel pipeline-results-priority"
      aria-labelledby="pipeline-analysis-first-heading">
      <div className="pipeline-section-heading">
        <div>
          <span className="eyebrow">이번 Pipeline Run · 분석과 결과</span>
          <h3 id="pipeline-analysis-first-heading" tabIndex={-1}>학습부터 Test까지 살펴보기</h3>
          <p>Train·Calibration·Test의 실제 점수 분포와 판정 근거를 먼저 확인한 뒤,
            관심 있는 Test 후보를 더 자세히 검토하세요.</p>
        </div>
      </div>
      <PipelineAnalysis run={run} onOpenResource={onOpenResource}
        initialSection={initialAnalysisSection} onSectionChange={onSelectAnalysisSection} />
      <div className="pipeline-actions">
        <button type="button" onClick={() =>
          moveFocusTo("pipeline-candidates-heading")}>이상 후보 상세 검토 →</button>
        <button type="button" className="secondary" onClick={() =>
          selectRun("")}>다른 Dataset으로 새 분석 →</button>
      </div>
    </section>}
    {error && <div className="alert error" role="alert">{error}
      <button className="ghost" onClick={() => setError("")}>닫기</button></div>}

    <section className="panel pipeline-start" aria-labelledby="pipeline-start-title">
      <div className="pipeline-section-heading">
        <div><span className="eyebrow">01 · 분석 이해</span>
          <h3 id="pipeline-start-title">{current?.name ?? "시간 기반 활동 이상 탐지"}</h3>
          <p>{current?.description ?? "등록된 분석 방법의 정의를 불러오고 있습니다."}</p>
        </div>
        <span className="chip">등록형 Reference · {current ? "이용 가능" :
          catalogError ? "정의 조회 실패" : "조회 중"}</span>
      </div>
      {catalogError && <div className="pipeline-readiness-blocked" role="alert">
        등록된 Pipeline의 지원 조건을 조회하지 못했습니다: {catalogError}
        <button type="button" className="secondary" onClick={() =>
          setListReload(n => n + 1)}>Pipeline 정의 다시 조회</button>
      </div>}
      <div className="pipeline-facts">
        <div><strong>어떤 데이터?</strong><p>사용자·호스트 활동을 시간 창으로 집계한 Feature 데이터</p></div>
        <div><strong>어떤 작업?</strong><p>새 모델 학습, 보정 점수 계산, 정책 생성, 별도 Test 점수화와 평가</p></div>
        <div><strong>어떤 결과?</strong><p>시간 구간별 실행 근거, 점수와 임계값을 적용한 검토 후보</p></div>
      </div>
      <p className="pipeline-scope">이상 후보는 실제 침해·이상 사건의 확정이나 탐지 정확도 보증이 아닙니다.
        이 Pipeline은 다른 사용자의 모델을 재사용하지 않고 선택한 데이터로 새 모델을 학습합니다.</p>
      <details className="pipeline-evidence">
        <summary>분석 방법·지원 범위 자세히 보기</summary>
        {current ? <>
          <p>알고리즘: {current.algorithm} · Feature {count(current.required_features.length)}개 ·
            시간 컬럼 {current.required_time_column} · 레이블 불필요</p>
          <ul>{current.limitations.map((limitation, i) => <li key={i}>{limitation}</li>)}</ul>
          <details><summary>필수 Feature 이름 전체 확인</summary>
            <p className="pipeline-feature-list">{current.required_features.join(" · ")}</p>
          </details>
        </> : <p>등록형 Pipeline 정의를 확인할 수 없습니다. 잠시 후 페이지를 다시 열어 보세요.</p>}
      </details>
    </section>

    <section className="panel pipeline-prepare" aria-labelledby="pipeline-prepare-title">
      <div className="pipeline-section-heading">
        <div><span className="eyebrow">02 · 데이터 연결과 준비</span>
          <h3 id="pipeline-prepare-title" tabIndex={-1}>내 데이터로 시작하기</h3>
          <p>데이터가 없다면 먼저 등록하세요. 준비된 Dataset을 선택했다면 입력 조건을 확인하세요.</p>
        </div>
        <span className="chip">{!datasetId ? "Dataset 선택 전" :
          currentCheck?.ready ? "입력 구조 확인됨" :
          currentCheck ? "입력 조건 확인 필요" : "검사 전"}</span>
      </div>
      <div className="pipeline-input-guide">
        <strong>이 분석에 필요한 데이터</strong>
        <p>등록된 <strong>{current ? count(current.required_features.length) : "44"}개 수치형 활동 Feature</strong>와
          시간대가 명시된 <code>ts</code> 컬럼이 필요합니다.
          임의의 원본 로그만으로 이 Feature들을 자동 생성하는 단계는 현재 등록되어 있지 않습니다.</p>
        <p>이미 Feature가 준비된 데이터는 바로 사용할 수 있고, 그렇지 않다면 데이터를 확인하거나
          직접 지원되는 변환 기능을 이용해 준비할 수 있습니다. 다만 기존 변환 도구가 필요한 Feature를
          모두 만들어준다는 의미는 아닙니다.</p>
      </div>
      <div className="form-row">
        <label>분석할 Dataset
          <select aria-label="Pipeline 분석 Dataset" value={datasetId} onChange={e => {
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
        <button className="secondary" type="button" onClick={() => onNavigate("understanding", datasetId)}>
          Dataset 등록·내용 확인 →
        </button>
        <button className="secondary" type="button" onClick={() => onNavigate("preparation", datasetId)}>
          독립 데이터 준비 도구 →
        </button>
        <button type="button" disabled={!current || !datasetId || busy}
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
        <p>설계된 방법: Train 구간으로 모델 학습·중앙값 결측 처리 → Calibration 상위 0.5% 정책
          → 별도 Test 구간 적용. 실제 Test 후보 비율은 0.5%와 다를 수 있습니다.</p>
        <button type="button" disabled={!canRun || busy} onClick={() => void start()}>
          {busy ? "제출 중…" : "이 데이터로 분석 실행하기 →"}
        </button>
        {!canRun && <p>현재 계정은 Job 제출 권한이 없습니다.</p>}
      </div>}
    </section>

    <section className="panel pipeline-results" aria-labelledby="pipeline-run-heading">
      <div className="pipeline-section-heading">
        <div><span className="eyebrow">03 · 실행부터 결과까지</span>
          <h3 id="pipeline-run-heading">{run ? "내 분석 실행" : "실행 후에는 이렇게 확인합니다"}</h3>
          <p>{run ? "선택한 실행의 현재 상태와 결과를 확인하세요. 화면을 떠나도 같은 Run을 다시 열 수 있습니다." :
            "Dataset 검사와 실행을 마치면, 이 자리에서 결과 요약·후보·실행 근거를 확인할 수 있습니다."}</p>
        </div>
        {run && <span className="chip">{run.status}{run.cancel_requested ? " · 취소 요청됨" : ""}</span>}
      </div>
      {!run && <div className="pipeline-next-step" role={runLoadError ? "alert" : "status"}>
        <strong>{runId ? runLoadError ? "선택한 실행을 조회하지 못했습니다." :
          "선택한 실행을 불러오는 중입니다." : "아직 선택된 실행이 없습니다."}</strong>
        {runId && runLoadError && <>
          <p>{runLoadError}</p>
          <div className="pipeline-actions">
            <button type="button" className="secondary" onClick={() =>
              setRunReload(n => n + 1)}>이 Run 다시 조회하기</button>
            <button type="button" className="secondary" onClick={() => selectRun("")}>
              새 분석 준비하기 →
            </button>
          </div>
        </>}
        {!runId && <p>위에서 Dataset을 선택하고 입력 구조를 확인한 뒤 명시적으로 실행하거나,
          아래의 이전 Run을 열어 보세요.</p>}
      </div>}
      {run && <>
        {run.error_code && <p className="pipeline-run-failure-code" role="alert">
          실패 코드: {run.error_code}</p>}
        {run.status === "Failed" && <div className="pipeline-readiness-blocked" role="alert">
          <strong>분석이 완료되지 않았습니다.</strong>
          <p>위 오류 코드와 단계별 근거를 확인하세요. 입력 조건 문제라면 Dataset 내용을 확인하고,
            다른 Dataset이나 수정된 판본으로 다시 검사해 새 Run을 제출할 수 있습니다.</p>
          <button type="button" className="secondary" onClick={() => onNavigate("understanding", datasetId)}>
            Dataset 확인 →
          </button>
        </div>}
        {run.status === "Cancelled" && <p className="pipeline-next-step" role="status">
          실행 취소가 확정되었습니다. 기존 기록은 보존되며 필요하다면 새 Run을 실행할 수 있습니다.
        </p>}
        {!terminal.has(run.status) && <div className="pipeline-next-step" role="status">
          <strong>{run.cancel_requested ? "취소 요청을 처리하고 있습니다." :
            run.status === "Queued" ? "분석 실행을 기다리고 있습니다." :
            activeStage ? activeStage + " 단계 진행 중" : "실행 대기 또는 단계 전환 중"}</strong>
          <p>서버 Worker에서 실제 Job 상태를 확인합니다. 완료 시간을 추정한 진행률로 표현하지 않습니다.</p>
          {canCancel && <button type="button" className="secondary"
            disabled={busy || run.cancel_requested} onClick={() => void cancel()}>
            Pipeline 취소 요청
          </button>}
        </div>}
        {run.status === "Succeeded" && <section className="pipeline-candidates" aria-labelledby="pipeline-candidates-heading">
          <h4 id="pipeline-candidates-heading" tabIndex={-1}>Test 이상 후보 살펴보기</h4>
          <p>보정 구간에서 정한 점수 경계 이상인 Test 결과를 순서대로 표시합니다.
            결과의 행 번호는 Scoring 내부 번호이며 원본 관측치 식별자가 아닙니다.</p>
          {!rows && candidateError && <div className="pipeline-readiness-blocked" role="alert">
            <strong>후보 조회에 실패했습니다.</strong>
            <p>{candidateError}</p>
            <button type="button" className="secondary" onClick={() => {
              setCandidateError("");
              setCandidateReload(current => current + 1);
            }}>후보 다시 조회하기</button>
          </div>}
          {!rows && !candidateError && <p role="status">
            완료된 Run의 후보를 불러오는 중입니다.
          </p>}
          {rows && <>
            {rows.items.length === 0 && <p>현재 조회에서 이상 후보가 없습니다.</p>}
            {rows.items.length > 0 && <p className="pipeline-result-count">
              점수 순으로 현재 불러온 후보 {count(rows.items.length)}건 · 전체 후보 수는 위 Test 분석에서 확인
            </p>}
            {rows.items.length > 0 && <div className="pipeline-candidate-workspace">
              <div className="pipeline-candidate-list">
                <div className="table-scroll"><table>
              <thead><tr><th>결과 행 번호</th><th>이상 점수</th><th>선택·확인</th></tr></thead>
              <tbody>{rows.items.map(item => <tr key={item.row_index}
                className={selectedCandidate === item.row_index ? "pipeline-candidate-selected" : ""}>
                <td>{count(item.row_index)}</td>
                <td>{item.anomaly_score.toPrecision(6)}</td>
                <td><button type="button" className="secondary" aria-pressed={selectedCandidate === item.row_index}
                  onClick={() => selectCandidateRow(item.row_index)}>
                  {selectedCandidate === item.row_index ? "선택됨" : "이 후보 분석"}
                </button></td>
              </tr>)}</tbody>
                </table></div>
                {rows.next_cursor && <button type="button" className="secondary" disabled={busy}
                  onClick={() => void more()}>다음 후보 25건 보기</button>}
              </div>
              <div className="pipeline-candidate-detail">
                {selectedCandidate !== null && !activeCandidate && <div className="pipeline-next-step" role="status">
                  <strong>선택했던 후보 #{count(selectedCandidate)}를 찾는 중입니다.</strong>
                  <p>선택한 후보는 현재 불러온 점수 순위 범위에 없습니다.
                    {rows.next_cursor ? " 다음 후보를 불러오면 계속 확인할 수 있습니다." :
                    " 이 Run에서 해당 후보를 찾지 못했습니다."}</p>
                </div>}
                {activeCandidate ? <PipelineCandidateInvestigation
                  key={run.id + ":" + rows.result_id + ":" + activeCandidate.row_index}
                  candidate={activeCandidate} scoringResultId={rows.result_id}
                  canRun={canRun} onOpenResource={onOpenResource} />
                  : selectedCandidate === null ? <div className="pipeline-next-step" role="status">
                    <strong>검토할 후보를 선택하세요.</strong>
                    <p>왼쪽 목록에서 점수를 확인하고 후보를 선택하면 Feature 값과
                      설명 계산을 같은 작업 공간에서 살펴볼 수 있습니다.</p>
                  </div> : null}
              </div>
            </div>}
          </>}
        </section>}
        <details className="pipeline-evidence">
          <summary>단계별 실행 상태·분할 범위·기술 근거</summary>
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
                <span>{entry ? entry.job.status : run.current_stage === stage ? "대기" : "미시작"}</span>
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
      <details className="pipeline-history">
        <summary>이전 Pipeline Run 목록 ({count(history.length)}건 조회됨)</summary>
        <div className="ml-resource-list">
          {history.map(item => <button type="button" className="platform-list-link ghost" key={item.id}
            onClick={() => selectRun(item.id)}>
            {datasets.find(data => data.id === item.dataset_id)?.name ?? item.dataset_id.slice(0, 12)}
            {" · "}{item.status}{" · "}{new Date(item.created_at).toLocaleString("ko-KR")}
          </button>)}
          {history.length === 0 && <p className="muted">{
            historyError ? "Run 목록을 확인하지 못했습니다. 상단에서 다시 조회하세요." :
              "조회된 Run이 없습니다. 새 분석을 실행할 수 있습니다."
          }</p>}
        </div>
      </details>
    </section>
  </div>;
}
