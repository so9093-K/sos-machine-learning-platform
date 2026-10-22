import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import type { Dataset } from "./types";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

type Definition = {
  id: string; version: string; name: string; description: string;
  required_features: string[]; stages: string[]; limitations: string[];
};
type Check = {
  ready: boolean; issues: string[]; dataset_id: string; dataset_version: string;
  feature_count: number;
  plan: null | {
    train_end: string; calibration_end: string;
    partition_sizes: { train: number; calibration: number; test: number };
  };
};
type Job = { id: string; kind: string; status: string; error_code?: string | null;
  result?: Record<string, string> | null; };
type Run = {
  id: string; definition_id: string; dataset_id: string; dataset_version: string;
  status: string; cancel_requested: boolean; error_code?: string | null;
  current_stage: string | null; created_at: string;
  stages?: { name: string; job: Job }[];
};
type CandidatePage = {
  items: { row_index: number; anomaly_score: number; features: Record<string, number | null> }[];
  threshold: number; next_cursor: string | null; result_id: string;
};
const PIPELINE = "activity-anomaly-if";
const stageLabel: Record<string,string> = {
  training: "모델 학습", calibration: "Calibration 점수화",
  policy: "판정 기준 생성", test: "Test 점수화", evaluation: "정책 적용 평가",
};
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
const count = (value: number) => value.toLocaleString("ko-KR");

/** 등록된 분석 방법을 이해·검사·실행하고 자신의 결과로 이동하는 경험. */
export function PipelineWorkspace({ datasets, canRun, canCancel, onNavigate, onOpenResource }: {
  datasets: Dataset[]; canRun: boolean; canCancel: boolean;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [catalog, setCatalog] = useState<Definition[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [checked, setChecked] = useState<Check | null>(null);
  const [runId, setRunId] = useState(() => new URLSearchParams(window.location.search).get("run") ?? "");
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [rows, setRows] = useState<CandidatePage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void Promise.all([
      request<Definition[]>("/api/pipelines"),
      request<Run[]>("/api/pipeline-runs"),
    ]).then(([definitions, runs]) => {
      if (active) { setCatalog(definitions); setHistory(runs); }
    }).catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, []);
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
        if (active) setRun(next);
        if (active && !terminal.has(next.status)) handle = setTimeout(() => void refresh(), 2400);
      } catch (e) {
        if (active) setError(formatError(e));
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(handle); };
  }, [runId]);
  useEffect(() => {
    if (run?.status !== "Succeeded") return;
    let active = true;
    void request<CandidatePage>(`/api/pipeline-runs/${encodeURIComponent(run.id)}/candidates?limit=25`)
      .then(page => { if (active) setRows(page); })
      .catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, [run?.id, run?.status]);
  function selectRun(id: string) {
    setRunId(id); setRows(null); setError("");
    window.history.replaceState(null, "", "/pipelines?run=" + encodeURIComponent(id));
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
    setBusy(true); setError("");
    try {
      const next = await submitJob<Run>(`/api/pipelines/${PIPELINE}/runs`, { dataset_id: datasetId });
      selectRun(next.id);
      setRun(next);
      void request<Run[]>("/api/pipeline-runs").then(setHistory).catch(() => {});
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  async function cancel() {
    if (!run || terminal.has(run.status) || busy || !canCancel) return;
    setBusy(true); setError("");
    try {
      setRun(await request<Run>(
        `/api/pipeline-runs/${encodeURIComponent(run.id)}/cancel`,
        { method: "POST" },
      ));
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  async function more() {
    if (!run || !rows?.next_cursor || busy) return;
    setBusy(true); setError("");
    try {
      const next = await request<CandidatePage>(
        `/api/pipeline-runs/${encodeURIComponent(run.id)}/candidates?limit=25&cursor=${encodeURIComponent(rows.next_cursor)}`
      );
      setRows(old => old ? { ...next, items: [...old.items, ...next.items] } : next);
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  const selected = datasets.find(item => item.id === datasetId);
  const current = catalog.find(item => item.id === PIPELINE);
  const completed = run?.stages ?? [];
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">REGISTERED PIPELINE</span>
      <h2>등록된 ML 분석 파이프라인</h2>
      <p>분석 방법을 직접 구성하지 않고, 자신의 데이터를 선택해 입력 조건을 확인한 다음 실행할 수 있습니다. 기존 개별 ML 기능도 계속 사용할 수 있습니다.</p>
    </section>
    {error && <div className="alert error" role="alert">{error}
      <button className="ghost" onClick={() => setError("")}>닫기</button></div>}
    <section className="panel">
      <h3>{current?.name ?? "시간 기반 활동 이상 탐지"}</h3>
      <p>{current?.description ?? "등록형 Pipeline 정의를 조회하는 중입니다."}</p>
      {current && <details><summary>지원 입력 · 분석 범위</summary>
        <p>입력 Feature {count(current.required_features.length)}개 · 명시적 UTC 시간 컬럼 ts · 사용자 Dataset으로 새 모델 학습</p>
        <p className="hint">{current.limitations.join(" ")}</p>
      </details>}
      <div className="form-row">
        <label>내 Dataset
          <select value={datasetId} onChange={e => setDatasetId(e.target.value)}>
            <option value="">Dataset 선택</option>
            {datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
      </div>
      <div className="actions">
        <button className="secondary" onClick={() => onNavigate("understanding")}>데이터 등록·탐색 →</button>
        <button disabled={!current || !datasetId || busy} onClick={() => void inspect()}>
          {busy ? "확인 중…" : "입력 적합성 확인"}
        </button>
      </div>
      {selected && <p className="hint">선택한 Dataset 판본: {selected.version.slice(0, 16)} · 입력 검사는 데이터를 읽으며 분석 Job을 자동 제출하지 않습니다.</p>}
      {checked && !checked.ready && <div role="alert" className="alert error">
        <strong>이 Dataset은 아직 실행할 수 없습니다.</strong>
        <ul>{checked.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
      </div>}
      {checked?.ready && checked.plan && <div className="steps" role="status">
        <strong>분석 계획 · 입력 적합</strong>
        <p>Train {count(checked.plan.partition_sizes.train)}행 · Calibration {count(checked.plan.partition_sizes.calibration)}행 · Test {count(checked.plan.partition_sizes.test)}행</p>
        <p className="hint">Train 끝: {new Date(checked.plan.train_end).toLocaleString("ko-KR")} · Calibration 끝: {new Date(checked.plan.calibration_end).toLocaleString("ko-KR")}</p>
        <p className="hint">등록된 파이프라인의 44개 수치 Feature와 Train-only 중앙값 전처리를 사용합니다. Calibration 기준 0.5% 후보 정책을 Test에 적용하며 실제 Test 후보율은 다를 수 있습니다.</p>
        <button disabled={!canRun || busy} onClick={() => void start()}>
          {busy ? "제출 중…" : "이 Dataset으로 Pipeline 실행"}
        </button>
      </div>}
    </section>
    <section className="panel">
      <h3>내 Pipeline 실행</h3>
      {run ? <>
        <p>Run {run.id} · <strong>{run.status}</strong>
          {run.cancel_requested ? " · 취소 요청됨" : ""}
          {run.error_code ? " · 오류 코드 " + run.error_code : ""}</p>
        <p className="hint">실행은 서버 Worker에서 진행됩니다. 화면을 닫아도 Run을 다시 조회할 수 있습니다. 표시할 근거 없는 완료율은 추정하지 않습니다.</p>
        {!terminal.has(run.status) && canCancel &&
          <button className="secondary" disabled={busy || run.cancel_requested} onClick={() => void cancel()}>Pipeline 취소 요청</button>}
        <div className="job-list">
          {current?.stages.map(stage => {
            const entry = completed.find(item => item.name === stage);
            return <div className="ml-resource-row" key={stage}>
              <strong>{stageLabel[stage] ?? stage}</strong>
              <span>{entry ? entry.job.status : run.current_stage === stage ? "대기" : "미시작"}</span>
              {entry?.job.error_code && <small>{entry.job.error_code}</small>}
              {entry?.job.result?.model_revision_id && <button className="secondary"
                onClick={() => onOpenResource("model", entry.job.result!.model_revision_id!)}>모델 결과 →</button>}
              {entry?.job.result?.scoring_result_id && <button className="secondary"
                onClick={() => onOpenResource("scoring", entry.job.result!.scoring_result_id!)}>Scoring 결과 →</button>}
              {entry?.job.result?.evaluation_revision_id && <button className="secondary"
                onClick={() => onOpenResource("evaluation", entry.job.result!.evaluation_revision_id!)}>평가 결과 →</button>}
            </div>;
          })}
        </div>
        {run.status === "Succeeded" && <div className="steps">
          <h4>내 데이터의 이상 후보</h4>
          <p className="hint">Calibration에서 고정한 임계값 이상인 Test 점수만 표시합니다. 점수는 확률·정답 레이블이 아닙니다. 행 번호는 Scoring 결과 내부 번호이며 원본 관측치 ID가 아닙니다.</p>
          {rows && <>
            <p>후보 경계 점수: {rows.threshold.toPrecision(6)} · 조회한 후보 {count(rows.items.length)}건</p>
            <div className="table-scroll"><table>
              <thead><tr><th>결과 내 행 번호</th><th>이상 점수</th><th>사용된 Feature 값</th></tr></thead>
              <tbody>{rows.items.map(item => <tr key={item.row_index}>
                <td>{count(item.row_index)}</td>
                <td>{item.anomaly_score.toPrecision(6)}</td>
                <td><details><summary>Feature 확인</summary><pre>{JSON.stringify(item.features, null, 2)}</pre></details></td>
              </tr>)}</tbody>
            </table></div>
            {rows.items.length === 0 && <p>설정한 임계값을 넘는 Test 후보가 없습니다.</p>}
            {rows.next_cursor && <button className="secondary" disabled={busy} onClick={() => void more()}>후보 25건 더 보기</button>}
          </>}
        </div>}
      </> : <p className="muted">Pipeline 실행을 선택하거나 이전 Run을 열어보세요.</p>}
      <h4>최근 실행</h4>
      <div className="ml-resource-list">
        {history.map(item => <button className="platform-list-link ghost" key={item.id}
          onClick={() => selectRun(item.id)}>
          {item.id.slice(0, 12)} · {item.status} · {new Date(item.created_at).toLocaleString("ko-KR")}
        </button>)}
        {history.length === 0 && <p className="muted">조회된 Pipeline Run이 없습니다.</p>}
      </div>
    </section>
  </div>;
}
