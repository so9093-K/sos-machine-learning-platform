import { useEffect, useRef, useState } from "react";
import { ApiError, formatError, request, submitJob } from "./api";
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
export function PipelineWorkspace({ datasets, initialDatasetId, initialRunId, hasMoreDatasets, onLoadMoreDatasets, onSelectDataset, onSelectRun, canRun, canCancel, onNavigate, onOpenResource }: {
  datasets: Dataset[]; initialDatasetId: string; initialRunId: string;
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  onSelectDataset: (datasetId: string) => void;
  onSelectRun: (runId: string, datasetId: string) => void;
  canRun: boolean; canCancel: boolean;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [catalog, setCatalog] = useState<Definition[]>([]);
  const [datasetId, setDatasetId] = useState(initialDatasetId);
  const [checked, setChecked] = useState<Check | null>(null);
  const [runId, setRunId] = useState(initialRunId);
  const selectedRunId = useRef(runId);
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [rows, setRows] = useState<CandidatePage | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingDatasets, setLoadingDatasets] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    // Run 이력이 일시적으로 실패해도 Pipeline Catalog 자체는 이용할 수 있다.
    void request<Definition[]>("/api/pipelines")
      .then(definitions => { if (active) setCatalog(definitions); })
      .catch(e => { if (active) setError(formatError(e)); });
    void request<Run[]>("/api/pipeline-runs")
      .then(runs => { if (active) setHistory(runs); })
      .catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    setDatasetId(initialDatasetId);
  }, [initialDatasetId]);
  useEffect(() => {
    if (selectedRunId.current !== initialRunId) {
      selectedRunId.current = initialRunId;
      setRunId(initialRunId);
      setRun(null);
      setRows(null);
      setError("");
    }
  }, [initialRunId]);
  useEffect(() => {
    setChecked(null);
    setRows(null);
  }, [datasetId]);
  useEffect(() => {
    if (!runId) { setRun(null); return; }
    let active = true;
    let handle: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await request<Run>(`/api/pipeline-runs/${encodeURIComponent(runId)}`);
        if (active && selectedRunId.current === runId) setRun(next);
        if (active && selectedRunId.current === runId && !terminal.has(next.status)) {
          handle = setTimeout(() => void refresh(), 2400);
        }
      } catch (e) {
        if (active && selectedRunId.current === runId) {
          setError(formatError(e));
          // 권한 거부·존재하지 않는 Run에는 반복 요청하지 않는다.
          const permanent = e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429;
          if (!permanent) handle = setTimeout(() => void refresh(), 5000);
        }
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(handle); };
  }, [runId]);
  useEffect(() => {
    if (run?.status !== "Succeeded") return;
    let active = true;
    void request<CandidatePage>(`/api/pipeline-runs/${encodeURIComponent(run.id)}/candidates?limit=25`)
      .then(page => { if (active && selectedRunId.current === run.id) setRows(page); })
      .catch(e => { if (active && selectedRunId.current === run.id) setError(formatError(e)); });
    return () => { active = false; };
  }, [run?.id, run?.status]);
  function selectRun(id: string) {
    selectedRunId.current = id;
    setRunId(id); setRun(null); setRows(null); setError(""); setBusy(false);
    onSelectRun(id, datasetId);
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
    setBusy(true); setError(""); setChecked(null);
    try {
      setChecked(await request<Check>(
        `/api/pipelines/${PIPELINE}/check?dataset_id=${encodeURIComponent(datasetId)}`
      ));
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  async function start() {
    if (!datasetId || checked?.dataset_id !== datasetId || !checked.ready || busy || !canRun) return;
    const previousRunId = selectedRunId.current;
    setBusy(true); setError("");
    try {
      const next = await submitJob<Run>(`/api/pipelines/${PIPELINE}/runs`, { dataset_id: datasetId });
      // 제출 중 다른 Run을 선택했다면 늦은 응답으로 사용자의 화면을 전환하지 않는다.
      if (selectedRunId.current === previousRunId) {
        selectRun(next.id);
        setRun(next);
      }
      void request<Run[]>("/api/pipeline-runs").then(setHistory).catch(() => {});
    } catch (e) {
      if (selectedRunId.current === previousRunId) setError(formatError(e));
    } finally {
      if (selectedRunId.current === previousRunId) setBusy(false);
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
  const modelRevisionId = resultId(completed.find(item => item.name === "training")?.job, "model_revision_id");
  const testScoringId = resultId(completed.find(item => item.name === "test")?.job, "scoring_result_id");
  const evaluationId = resultId(completed.find(item => item.name === "evaluation")?.job, "evaluation_revision_id");
  const inputReady = Boolean(currentCheck?.ready);
  const activeStage = run?.current_stage ? stageLabel[run.current_stage] ?? run.current_stage : null;
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
    </section>

    <nav className="pipeline-journey" aria-label="분석 이용 단계">
      <div className="pipeline-journey-item"><span>01</span><strong>분석 이해</strong><small>목적·입력·결과</small></div>
      <div className="pipeline-journey-item"><span>02</span><strong>데이터 준비</strong><small>등록·선택·확인</small></div>
      <div className="pipeline-journey-item"><span>03</span><strong>실행</strong><small>상태·취소·복구</small></div>
      <div className="pipeline-journey-item"><span>04</span><strong>결과 활용</strong><small>후보·상세 근거</small></div>
    </nav>
    {error && <div className="alert error" role="alert">{error}
      <button className="ghost" onClick={() => setError("")}>닫기</button></div>}

    <section className="panel pipeline-start" aria-labelledby="pipeline-start-title">
      <div className="pipeline-section-heading">
        <div><span className="eyebrow">01 · 분석 이해</span>
          <h3 id="pipeline-start-title">{current?.name ?? "시간 기반 활동 이상 탐지"}</h3>
          <p>{current?.description ?? "등록된 분석 방법의 정의를 불러오고 있습니다."}</p>
        </div>
        <span className="chip">등록형 Reference · {current ? "이용 가능" : "조회 중"}</span>
      </div>
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
          <h3 id="pipeline-prepare-title">내 데이터로 시작하기</h3>
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
            setDatasetId(e.target.value);
            onSelectDataset(e.target.value);
            setError("");
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
        <button className="secondary" type="button" onClick={() => onNavigate("understanding")}>
          Dataset 등록·내용 확인 →
        </button>
        <button className="secondary" type="button" onClick={() => onNavigate("preparation")}>
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
          <button type="button" className="secondary" onClick={() => onNavigate("understanding")}>
            Dataset 구조 확인 →
          </button>
          <button type="button" className="secondary" onClick={() => onNavigate("preparation")}>
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
      {!run && <div className="pipeline-next-step">
        <strong>아직 선택된 실행이 없습니다.</strong>
        <p>위에서 Dataset을 선택하고 입력 구조를 확인한 뒤 명시적으로 실행하거나,
          아래의 이전 Run을 열어 보세요.</p>
      </div>}
      {run && <>
        <div className="pipeline-run-identity">
          <p><strong>Run</strong> <code>{run.id}</code></p>
          <p><strong>Dataset</strong> {datasets.find(item => item.id === run.dataset_id)?.name ?? run.dataset_id}</p>
          <p><strong>현재 상태</strong> {run.status}{activeStage ? " · " + activeStage : ""}</p>
          {run.error_code && <p><strong>실패 코드</strong> {run.error_code}</p>}
        </div>
        {run.status === "Failed" && <div className="pipeline-readiness-blocked" role="alert">
          <strong>분석이 완료되지 않았습니다.</strong>
          <p>위 오류 코드와 단계별 근거를 확인하세요. 입력 조건 문제라면 Dataset 내용을 확인하고,
            다른 Dataset이나 수정된 판본으로 다시 검사해 새 Run을 제출할 수 있습니다.</p>
          <button type="button" className="secondary" onClick={() => onNavigate("understanding")}>
            Dataset 확인 →
          </button>
        </div>}
        {run.status === "Cancelled" && <p className="pipeline-next-step" role="status">
          실행 취소가 확정되었습니다. 기존 기록은 보존되며 필요하다면 새 Run을 실행할 수 있습니다.
        </p>}
        {!terminal.has(run.status) && <div className="pipeline-next-step" role="status">
          <strong>{run.cancel_requested ? "취소 요청을 처리하고 있습니다." :
            activeStage ? activeStage + " 단계 진행 중" : "실행 대기 또는 단계 전환 중"}</strong>
          <p>서버 Worker에서 실제 Job 상태를 확인합니다. 완료 시간을 추정한 진행률로 표현하지 않습니다.</p>
          {canCancel && <button type="button" className="secondary"
            disabled={busy || run.cancel_requested} onClick={() => void cancel()}>
            Pipeline 취소 요청
          </button>}
        </div>}
        {run.status === "Succeeded" && <div className="pipeline-result-overview" aria-label="분석 결과 요약">
          <h4>완료된 분석의 핵심 결과</h4>
          {run.input_plan && <div className="pipeline-result-metrics">
            <div><span>Train 입력</span><strong>{count(run.input_plan.partition_sizes.train)}행</strong></div>
            <div><span>Calibration 입력</span><strong>{count(run.input_plan.partition_sizes.calibration)}행</strong></div>
            <div><span>Test 입력</span><strong>{count(run.input_plan.partition_sizes.test)}행</strong></div>
          </div>}
          <div className="pipeline-result-metrics">
            <div><span>현재 조회된 Test 후보</span><strong>{rows ? count(rows.items.length) + "건" : "조회 중"}</strong></div>
            <div><span>후보 경계 점수</span><strong>{rows ? rows.threshold.toPrecision(6) : "조회 중"}</strong></div>
          </div>
          <p className="hint">후보 건수는 현재 조회한 페이지의 수이며 전체 후보 수가 아닙니다.
            이상 점수와 정책 판정은 사건 확정이나 정확도 지표가 아닙니다.</p>
          <div className="pipeline-actions">
            {testScoringId && <button type="button" className="secondary"
              onClick={() => onOpenResource("scoring", testScoringId)}>전체 Scoring 결과 확인 →</button>}
            {evaluationId && <button type="button" className="secondary"
              onClick={() => onOpenResource("evaluation", evaluationId)}>평가 근거 확인 →</button>}
            {modelRevisionId && <button type="button" className="secondary"
              onClick={() => onOpenResource("model", modelRevisionId)}>모델 판본 확인 →</button>}
          </div>
        </div>}
        {run.status === "Succeeded" && <section className="pipeline-candidates" aria-labelledby="pipeline-candidates-heading">
          <h4 id="pipeline-candidates-heading">Test 이상 후보 살펴보기</h4>
          <p>보정 구간에서 정한 점수 경계 이상인 Test 결과를 순서대로 표시합니다.
            결과의 행 번호는 Scoring 내부 번호이며 원본 관측치 식별자가 아닙니다.</p>
          {!rows && <p role="status">완료된 Run의 후보를 불러오는 중입니다.
            조회 오류가 표시되면 Run을 다시 선택해 재조회할 수 있습니다.</p>}
          {rows && <>
            {rows.items.length === 0 && <p>현재 페이지에는 이상 후보가 없습니다.</p>}
            {rows.items.length > 0 && <div className="table-scroll"><table>
              <thead><tr><th>결과 행 번호</th><th>이상 점수</th><th>Feature 값</th></tr></thead>
              <tbody>{rows.items.map(item => <tr key={item.row_index}>
                <td>{count(item.row_index)}</td>
                <td>{item.anomaly_score.toPrecision(6)}</td>
                <td><details><summary>상세 Feature</summary>
                  <pre>{JSON.stringify(item.features, null, 2)}</pre></details></td>
              </tr>)}</tbody>
            </table></div>}
            {rows.next_cursor && <button type="button" className="secondary" disabled={busy}
              onClick={() => void more()}>다음 후보 25건 보기</button>}
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
        <summary>이전 Pipeline Run 찾아보기 ({count(history.length)}건 조회됨)</summary>
        <div className="ml-resource-list">
          {history.map(item => <button type="button" className="platform-list-link ghost" key={item.id}
            onClick={() => selectRun(item.id)}>
            {datasets.find(data => data.id === item.dataset_id)?.name ?? item.dataset_id.slice(0, 12)}
            {" · "}{item.status}{" · "}{new Date(item.created_at).toLocaleString("ko-KR")}
          </button>)}
          {history.length === 0 && <p className="muted">조회된 Run이 없습니다. 새 분석을 실행할 수 있습니다.</p>}
        </div>
      </details>
    </section>
  </div>;
}
