import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type * as echarts from "echarts";
import { DataChart, CHART_COLORS } from "./DataChart";
import { createStep, histogram, needsTimeOrder, type FeatureStep } from "./feature-utils";
import { ApiError, request, jsonPost, setAccessToken, formatError, uploadFile } from "./api";
import { ExampleGallery } from "./ExampleGallery";
import { JobsPanel } from "./JobsPanel";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import { DiscoveryPanel } from "./DiscoveryPanel";
import type { Dataset, Preview, Profile, Job, JobSummary, JobPage, ExplorationJobSummary, ExplorationJobPage, Example, UploadPolicy } from "./types";
import "./style.css";

type Step = FeatureStep;
type Tab = "preview" | "profile" | "features" | "jobs" | "storage" | "audit";
type Identity = { actor_id: string; actor_type: string; roles: string[]; mode: string };
type UploadProgress = {
  name: string; loaded: number; total: number;
  phase: "transferring" | "registering";
};
const mebibytes = (size: number) => (size / (1024 * 1024)).toFixed(2);

function readableVersion(d: Dataset) {
  return d.kind === "iceberg" ? `Snapshot ${d.version}` : `SHA256 ${d.version.slice(0, 12)}`;
}
function Workspace({ actor }: { actor: Identity }) {
  const canManage = actor.roles.some(r => ["administrator", "operator"].includes(r));
  const canWrite = actor.roles.some(r => ["administrator", "researcher"].includes(r));
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [nextJobsCursor, setNextJobsCursor] = useState<string | null>(null);
  const [discoveryJobs, setDiscoveryJobs] = useState<ExplorationJobSummary[]>([]);
  const [nextDiscoveryCursor, setNextDiscoveryCursor] = useState<string | null>(null);
  const [olderDiscoveryJobsLoaded, setOlderDiscoveryJobsLoaded] = useState(false);
  const [olderJobsLoaded, setOlderJobsLoaded] = useState(false);
  const [examples, setExamples] = useState<Example[]>([]);
  const [examplesLoading, setExamplesLoading] = useState(true);
  const [previewStatus, setPreviewStatus] = useState<"loading" | "ready" | "error">("loading");
  const [profileStatus, setProfileStatus] = useState<"loading" | "ready" | "error">("loading");
  const [reloadRevision, setReloadRevision] = useState(0);
  const [tab, setTab] = useState<Tab>("preview");
  const [busy, setBusy] = useState(false);
  const [uploadPolicy, setUploadPolicy] = useState<UploadPolicy | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tableName, setTableName] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [editingStep, setEditingStep] = useState<number | null>(null);
  const [lagPeriods, setLagPeriods] = useState(1);
  const [rollingWindow, setRollingWindow] = useState(3);
  const [op, setOp] = useState("filter_not_null");
  const [column, setColumn] = useState("");
  const [value, setValue] = useState("");
  const [orderBy, setOrderBy] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [featurePreviewResult, setFeaturePreview] = useState<{ signature: string; data: Preview } | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const dataset = datasets.find(d => d.id === selected);
  const columnNames = preview ? Object.keys(preview.schema) : [];
  const numeric = profile?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const [visualColumn, setVisualColumn] = useState("");
  const [timeColumn, setTimeColumn] = useState("");

  async function refreshDatasets() {
    const items = await request<Dataset[]>("/api/datasets");
    setDatasets(items);
    setSelected(previous => items.some(d => d.id === previous) ? previous : (items[0]?.id ?? ""));
  }
  /** 서버 등록 성공을 목록 새로고침 실패 때문에 취소된 작업처럼 표시하지 않는다. */
  async function showRegisteredDataset(data: Dataset, success: string, stale: string) {
    setDatasets(previous => [data, ...previous.filter(item => item.id !== data.id)]);
    setSelected(data.id); setTab("preview");
    try {
      await refreshDatasets();
      setNotice(success);
    } catch {
      setError("");
      setNotice(stale);
    }
  }
  /** HTTP 202 접수 이후의 목록 조회 실패는 Job 제출 실패가 아니다. */
  async function showSubmittedJob(job: Job, message: string) {
    setTab("jobs");
    try {
      await refreshJobs();
      setNotice(message);
    } catch {
      setError("");
      setNotice(`Job은 정상 접수됐습니다. 목록을 다시 확인하세요. 작업 ID: ${job.id}`);
    }
  }
  async function refreshJobs() {
    const page = await request<JobPage>("/api/jobs?limit=20");
    setJobs(previous => olderJobsLoaded
      ? [...page.items, ...previous.filter(job => !page.items.some(item => item.id === job.id))]
      : page.items);
    if (!olderJobsLoaded) setNextJobsCursor(page.next_cursor ?? null);
  }
  async function refreshDiscoveryJobs() {
    const page = await request<ExplorationJobPage>("/api/explorations/jobs?limit=20");
    setDiscoveryJobs(previous => olderDiscoveryJobsLoaded
      ? [...page.items, ...previous.filter(job => !page.items.some(item => item.id === job.id))]
      : page.items);
    if (!olderDiscoveryJobsLoaded) setNextDiscoveryCursor(page.next_cursor ?? null);
  }
  async function loadMoreDiscoveryJobs() {
    if (!nextDiscoveryCursor) return;
    await withFeedback(async () => {
      const page = await request<ExplorationJobPage>(
        `/api/explorations/jobs?limit=20&cursor=${encodeURIComponent(nextDiscoveryCursor)}`);
      setDiscoveryJobs(previous => [...previous, ...page.items.filter(
        item => !previous.some(job => job.id === item.id))]);
      setNextDiscoveryCursor(page.next_cursor ?? null);
      setOlderDiscoveryJobsLoaded(true);
    });
  }
  async function loadMoreJobs() {
    if (!nextJobsCursor) return;
    await withFeedback(async () => {
      const page = await request<JobPage>(
        `/api/jobs?limit=20&cursor=${encodeURIComponent(nextJobsCursor)}`);
      setJobs(previous => [...previous, ...page.items.filter(
        item => !previous.some(job => job.id === item.id))]);
      setNextJobsCursor(page.next_cursor ?? null);
      setOlderJobsLoaded(true);
    });
  }
  useEffect(() => {
    if (canWrite) {
      request<UploadPolicy>("/api/datasets/upload-policy")
        .then(setUploadPolicy).catch(e => setError(formatError(e)));
    }
    refreshDatasets().catch(e => setError(String(e)));
    refreshJobs().catch(e => setError(String(e)));
    refreshDiscoveryJobs().catch(e => setError(String(e)));
    request<Example[]>("/api/examples").then(setExamples)
      .catch(e => setError(String(e)))
      .finally(() => setExamplesLoading(false));
  }, []);
  useEffect(() => {
    // Dataset 전환과 네트워크 재시도를 구분해 작성 중인 변환을 보존한다.
    setFeaturePreview(null); setSteps([]); setEditingStep(null);
    setColumn(""); setVisualColumn(""); setTimeColumn(""); setOrderBy(""); setGroupBy("");
  }, [selected]);
  useEffect(() => {
    if (!selected) {
      setPreview(null); setProfile(null);
      return;
    }
    let active = true;
    setPreview(null); setProfile(null);
    setPreviewStatus("loading"); setProfileStatus("loading");
    request<Preview>(`/api/datasets/${selected}/preview?limit=100`)
      .then(data => {
        if (!active) return;
        setPreview(data); setPreviewStatus("ready");
        const columns = Object.keys(data.schema);
        setColumn(current => columns.includes(current) ? current : (columns[0] ?? ""));
        setVisualColumn(current =>
          columns.includes(current) && /Int|Float|Decimal/.test(data.schema[current])
            ? current : (columns.find(name => /Int|Float|Decimal/.test(data.schema[name]))
              ?? columns[0] ?? ""));
        setTimeColumn(current => columns.includes(current) ? current : (columns[0] ?? ""));
      })
      .catch(error => {
        if (!active) return;
        setPreviewStatus("error"); setError(String(error));
      });
    request<Profile>(`/api/datasets/${selected}/profile`)
      .then(data => { if (active) { setProfile(data); setProfileStatus("ready"); } })
      .catch(error => {
        if (!active) return;
        setProfileStatus("error"); setError(String(error));
      });
    return () => { active = false; };
  }, [selected, reloadRevision]);
  useEffect(() => {
    if (jobs.some(j => j.status === "Succeeded" && j.result?.dataset_id &&
      !datasets.some(d => d.id === j.result?.dataset_id))) {
      refreshDatasets().catch(e => setError(String(e)));
    }
  }, [jobs, datasets]);
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (jobs.some(j => !["Succeeded", "Failed", "Cancelled"].includes(j.status))) {
        refreshJobs().catch(e => setError(String(e)));
      }
      if (discoveryJobs.some(j => !["Succeeded", "Failed", "Cancelled"].includes(j.status))) {
        refreshDiscoveryJobs().catch(e => setError(String(e)));
      }
    }, 2000);
    return () => window.clearInterval(handle);
  }, [jobs, discoveryJobs]);

  async function withFeedback(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  function addStep() {
    try {
      const next = createStep(op, column, value, preview?.schema[column],
        op === "lag" ? lagPeriods : op === "rolling_mean" ? rollingWindow : undefined);
      setSteps(existing => editingStep === null ? [...existing, next]
        : existing.map((step, index) => index === editingStep ? next : step));
      setEditingStep(null); setFeaturePreview(null); setError("");
    } catch (e) { setError(formatError(e)); }
  }
  function editStep(index: number) {
    const step = steps[index];
    setEditingStep(index);
    setOp(step.op); setColumn(step.column ?? ""); setValue(step.value == null ? "" : String(step.value));
    setLagPeriods(step.periods ?? 1); setRollingWindow(step.window ?? 3);
  }
  function moveStep(index: number, direction: -1 | 1) {
    const destination = index + direction;
    if (destination < 0 || destination >= steps.length) return;
    setSteps(current => {
      const copy = [...current];
      [copy[index], copy[destination]] = [copy[destination], copy[index]];
      return copy;
    });
    setEditingStep(null); setFeaturePreview(null);
  }
  const transform = {
    steps,
    ...(orderBy ? { order_by: orderBy } : {}),
    ...(groupBy ? { group_by: [groupBy] } : {}),
  };
  // 오래된 응답은 현재 Dataset과 변환 설정에 맞을 때만 노출한다.
  const transformSignature = JSON.stringify({ dataset_id: selected, transform });
  const featurePreview = featurePreviewResult?.signature === transformSignature
    ? featurePreviewResult.data : null;
  async function previewFeatures() {
    if (!selected || steps.length === 0) return;
    if (needsTimeOrder(steps) && !orderBy) {
      setError("Lag와 Rolling 변환에는 정렬 컬럼을 지정해야 합니다."); return;
    }
    await withFeedback(async () => {
      const result = await jsonPost<Preview>("/api/features/preview",
        { dataset_id: selected, transform });
      setFeaturePreview({ signature: transformSignature, data: result });
    });
  }
  async function submit(kind: "profile" | "transform") {
    if (!selected) return;
    if (kind === "transform" && needsTimeOrder(steps) && !orderBy) {
      setError("Lag와 Rolling 변환에는 정렬 컬럼을 지정해야 합니다."); return;
    }
    await withFeedback(async () => {
      const job = await jsonPost<Job>("/api/jobs",
        { dataset_id: selected, kind, ...(kind === "transform" ? { transform } : {}) });
      await showSubmittedJob(job, "작업이 등록되었습니다. 별도 Worker가 작업을 실행합니다.");
    });
  }
  async function selectExample(exampleId: string) {
    await withFeedback(async () => {
      const data = await jsonPost<Dataset>(`/api/examples/${exampleId}/use`, {});
      await showRegisteredDataset(data,
        "예제가 일반 데이터셋으로 등록됐습니다. EDA와 피처 변환을 시작할 수 있습니다.",
        "예제는 정상 등록됐습니다. 목록 새로고침에 실패했지만 다시 등록할 필요는 없습니다.");
    });
  }
  async function openJobDataset(datasetId: string) {
    await refreshDatasets();
    setSelected(datasetId); setTab("preview");
  }
  async function upload(file: File) {
    if (uploadPolicy && !uploadPolicy.supported_extensions.some(ext =>
      file.name.toLowerCase().endsWith(ext.toLowerCase()))) {
      setNotice("");
      setError("서버가 지원하지 않는 파일 형식입니다. CSV 또는 Parquet 파일을 선택하세요.");
      return;
    }
    if (uploadPolicy && file.size > uploadPolicy.max_size_bytes) {
      setNotice("");
      setError(`파일 크기 ${mebibytes(file.size)} MiB가 최대 ${mebibytes(uploadPolicy.max_size_bytes)} MiB를 초과했습니다.`);
      return;
    }
    setUploadProgress({ name: file.name, loaded: 0,
      total: file.size, phase: "transferring" });
    await withFeedback(async () => {
      const data = await uploadFile<Dataset>(
        "/api/datasets/files", file,
        (loaded, total) => setUploadProgress(previous => previous
          ? { ...previous, loaded, total } : null),
        () => setUploadProgress(previous => previous
          ? { ...previous, phase: "registering" } : null),
      );
      // HTTP 201이 등록 성공의 정본이다.
      await showRegisteredDataset(data,
        "파일 등록이 완료됐습니다. 데이터 미리보기와 분석을 시작할 수 있습니다.",
        "파일은 정상 등록됐습니다. 데이터셋 목록 새로고침에 실패했지만 등록을 다시 시도할 필요는 없습니다.");
    });
    setUploadProgress(null);
  }
  async function connectExisting() {
    await withFeedback(async () => {
      const data = await jsonPost<Dataset>("/api/datasets/iceberg",
        { table_name: tableName });
      await showRegisteredDataset(data,
        "기존 Iceberg 테이블의 스냅샷을 연결했습니다.",
        "Iceberg 스냅샷은 정상 연결됐습니다. 목록 새로고침에 실패했지만 다시 연결할 필요는 없습니다.");
    });
  }
  async function importIceberg() {
    if (!selected) return;
    await withFeedback(async () => {
      const job = await jsonPost<Job>("/api/iceberg/import",
        { dataset_id: selected, table_name: tableName });
      await showSubmittedJob(job, "Iceberg 적재 Job이 등록되었습니다. 완료 후 데이터셋 목록에서 확인할 수 있습니다.");
    });
  }
  async function cancelJob(id: string) {
    await withFeedback(async () => {
      const path = discoveryJobs.some(job => job.id === id)
        ? `/api/explorations/jobs/${id}/cancel` : `/api/jobs/${id}/cancel`;
      await jsonPost(path, {});
      try {
        await Promise.all([refreshJobs(), refreshDiscoveryJobs()]);
      } catch {
        setError("");
        setNotice("취소 요청은 접수됐지만 상태 조회에 실패했습니다. 실제 종료 상태를 다시 확인하세요.");
      }
    });
  }
  const distribution = useMemo(() => histogram(preview?.rows ?? [], visualColumn),
    [preview, visualColumn]);
  const distributionOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!distribution) return null;
    return {
      backgroundColor: "transparent", textStyle: { color: CHART_COLORS.text },
      tooltip: { trigger: "axis" },
      grid: { left: 50, right: 15, top: 20, bottom: 48 },
      xAxis: { type: "category", data: distribution.labels,
        axisLabel: { color: CHART_COLORS.label, rotate: 20, hideOverlap: true } },
      yAxis: { type: "value", axisLabel: { color: CHART_COLORS.label },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } } },
      series: [{ type: "bar", data: distribution.counts,
        itemStyle: { color: CHART_COLORS.bar } }],
    };
  }, [distribution]);
  const chartOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!preview?.rows.length || !visualColumn) return null;
    const values = preview.rows.map(r => r[visualColumn])
      .filter((n): n is number => typeof n === "number" && Number.isFinite(n));
    if (!values.length) return null;
    return {
      backgroundColor: "transparent",
      textStyle: { color: CHART_COLORS.text },
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 20, top: 25, bottom: 42 },
      xAxis: { type: "category", data: preview.rows.map(r => String(r[timeColumn] ?? "")),
        axisLabel: { color: CHART_COLORS.label, hideOverlap: true } },
      yAxis: { type: "value", axisLabel: { color: CHART_COLORS.label }, splitLine: {
        lineStyle: { color: CHART_COLORS.grid } } },
      series: [{ type: "line", showSymbol: false, name: visualColumn, data: preview.rows.map(
        r => typeof r[visualColumn] === "number" ? r[visualColumn] : null
      ), lineStyle: { color: CHART_COLORS.line }, itemStyle: { color: CHART_COLORS.line } }],
    };
  }, [preview, visualColumn, timeColumn]);

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brandmark">S</div><div>
        <strong>SOS Platform</strong><small>Research · Execute · Evolve</small>
      </div></div>
      <div className="section-title">DATA SOURCES</div>
      {canWrite && <label className="upload" role="button" tabIndex={0} aria-label="CSV 또는 Parquet 파일 업로드"
        aria-disabled={busy} onKeyDown={event => {
          if (busy || !["Enter", " "].includes(event.key)) return;
          event.preventDefault(); uploadRef.current?.click();
        }}>
        + CSV / Parquet 가져오기
        {uploadPolicy && <small>최대 {mebibytes(uploadPolicy.max_size_bytes)} MiB</small>}
        <input ref={uploadRef} type="file"
          accept={uploadPolicy?.supported_extensions.join(",") ?? ".csv,.parquet"} disabled={busy}
          onChange={event => {
            const file = event.target.files?.[0]; if (file) void upload(file);
            event.target.value = "";
          }} />
      </label>}
      {canWrite && <div className="connect-existing">
        <label>기존 Iceberg 테이블
          <input aria-label="Iceberg 테이블 이름" value={tableName}
            onChange={e => setTableName(e.target.value)} placeholder="sensor_data" />
        </label>
        <button className="secondary" disabled={busy}
          onClick={() => void connectExisting()}>스냅샷 연결</button>
      </div>}
      <button className="job-navigation secondary" onClick={() => {
        setSelected(""); setTab("preview");
      }}>예제 데이터 살펴보기</button>
      <button className="job-navigation secondary" onClick={() => setTab("jobs")}>
        전체 Jobs 보기 {jobs.length > 0 ? `· ${jobs.length}` : ""}
      </button>
      {canManage && <button className="job-navigation secondary" onClick={() => setTab("storage")}>
        저장소 상태 보기
      </button>}
      {canManage && <button className="job-navigation secondary" onClick={() => setTab("audit")}>
        보안 감사 보기
      </button>}
      <nav aria-label="데이터셋 목록">
        {datasets.length === 0 && <p className="muted">등록된 데이터가 없습니다.</p>}
        {datasets.map(d => <button key={d.id} className={`dataset ${selected === d.id ? "active" : ""}`}
          onClick={() => { setSelected(d.id); setTab("preview"); }}>
          <span className="dataset-name">{d.name}</span>
          <span className="dataset-kind">{d.kind === "iceberg" ? "◆ Iceberg" : "◫ File"}</span>
        </button>)}
      </nav>
      <div className="sidebar-bottom"><a href="/docs" target="_blank" rel="noreferrer">↗ Scalar API Reference</a></div>
    </aside>
    <main className="main">
      <header><div><span className="eyebrow">DATA WORKSPACE</span>
        <h1>데이터를 이해하고, 준비하고, 실행하세요.</h1>
        <p>데이터 버전과 변환 조건을 남겨 동일한 연구를 다시 실행할 수 있습니다.</p></div>
        <span className="environment">Data Workspace · Python Worker</span>
      </header>
      {error && <div role="alert" className="alert error"><strong>작업을 완료하지 못했습니다.</strong> {error}
        <button onClick={() => setError("")} aria-label="오류 닫기">×</button></div>}
      {notice && <div role="status" className="alert success">{notice}
        <button onClick={() => setNotice("")} aria-label="알림 닫기">×</button></div>}
      {uploadProgress && <div className="upload-status" role="status" aria-live="polite">
        <strong>{uploadProgress.name}</strong>
        <span>{uploadProgress.phase === "transferring"
          ? `전송 중 · ${mebibytes(uploadProgress.loaded)} / ${mebibytes(uploadProgress.total)} MiB`
          : "전송 완료 · 서버에서 검증하고 Dataset을 등록하고 있습니다."}</span>
        {uploadProgress.phase === "transferring" && uploadProgress.total > 0 &&
          <progress max={uploadProgress.total} value={uploadProgress.loaded}
            aria-label="파일 전송 진행률" />}
      </div>}
      {!dataset && tab !== "jobs" && tab !== "storage" && tab !== "audit" && <div className="empty">
        <h2>첫 데이터를 탐색해 보세요</h2>
        <p>파일을 준비하지 않아도 합성 데이터로 EDA와 피처 실행을 경험할 수 있습니다.</p>
        <ExampleGallery examples={examples} loading={examplesLoading} busy={busy}
          onUse={id => void selectExample(id)} />
      </div>}
      {dataset && tab !== "jobs" && tab !== "storage" && tab !== "audit" && <>
        <div className="dataset-hero">
          <div><span className="eyebrow">SELECTED DATASET</span><h2>{dataset.name}</h2>
            <p>{dataset.kind.toUpperCase()} · {readableVersion(dataset)}</p></div>
          <div className="hero-actions">
            <button className="secondary" onClick={() => setReloadRevision(value => value + 1)}>
              데이터 다시 불러오기
            </button>
            {canWrite && <>
              <input aria-label="Iceberg 테이블명" value={tableName}
                onChange={e => setTableName(e.target.value)} placeholder="Iceberg table" />
              <button disabled={busy} className="secondary" onClick={() => void importIceberg()}>
                Iceberg에 추가
              </button>
            </>}</div>
        </div>
        <div className="tabbar" role="tablist" aria-label="데이터 작업 영역">
          {(["preview", "profile", "features"] as Tab[]).map(t =>
            <button key={t} role="tab" id={`sos-tab-${t}`}
              aria-controls={`sos-panel-${t}`} aria-selected={tab === t}
              tabIndex={tab === t ? 0 : -1}
              className={tab === t ? "tab active" : "tab"}
              onClick={() => setTab(t)}
              onKeyDown={event => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const tabs: Tab[] = ["preview", "profile", "features"];
                const index = tabs.indexOf(t);
                const next = event.key === "Home" ? tabs[0] :
                  event.key === "End" ? tabs[tabs.length - 1] :
                  tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
                setTab(next);
                requestAnimationFrame(() => document.getElementById(`sos-tab-${next}`)?.focus());
              }}>
              {{ preview: "Preview", profile: "EDA & Profile", features: "Features", jobs: "Jobs", storage: "Storage Health", audit: "Security Audit" }[t]}
            </button>)}
        </div>
        {tab === "preview" && <section className="panel" role="tabpanel"
          id="sos-panel-preview" aria-labelledby="sos-tab-preview" tabIndex={0}>
          <div className="panel-head"><div><h3>데이터 미리보기</h3><p>최대 100행 · 전체 데이터가 아닌 일부입니다.</p></div>
            <span className="chip">{preview?.returned_rows ?? "…"} rows shown</span></div>
          {preview && <><div className="table-scroll"><table><thead><tr>
            {columnNames.map(c => <th key={c}>{c}<small>{preview.schema[c]}</small></th>)}
          </tr></thead><tbody>{preview.rows.map((r, i) => <tr key={i}>
            {columnNames.map(c => <td key={c}>{r[c] == null
              ? <span className="null">NULL</span> : String(r[c])}</td>)}
          </tr>)}</tbody></table></div>{preview.rows.length === 0 && <p className="muted">행이 없는 데이터셋입니다.</p>}</>}
          {previewStatus === "loading" && <p className="muted" role="status">미리보기를 불러오는 중입니다.</p>}
          {previewStatus === "error" && <button className="secondary"
            onClick={() => setReloadRevision(v => v + 1)}>미리보기 다시 시도</button>}
          <div className="hint">스키마와 파일·스냅샷 버전은 실행 입력으로 고정됩니다.</div>
        </section>}
        {tab === "profile" && <section className="panel" role="tabpanel"
          id="sos-panel-profile" aria-labelledby="sos-tab-profile" tabIndex={0}>
          <DiscoveryPanel key={selected} datasetId={selected} canCreate={canWrite}
            onCreated={async () => { await refreshDiscoveryJobs(); setTab("jobs"); }} />
          <div className="panel-head"><div><h3>탐색적 데이터 분석</h3>
            <p>미리보기 통계는 상위 1,000행 기준입니다. 전체 분석은 Job으로 요청할 수 있습니다.</p>
          </div>{canWrite && <button disabled={busy} onClick={() => void submit("profile")}>전체 Profile 실행 ↗</button>}</div>
          {profileStatus === "loading" && <p role="status" className="muted">EDA 통계를 계산하고 있습니다.</p>}
          {profileStatus === "error" && <button className="secondary"
            onClick={() => setReloadRevision(v => v + 1)}>EDA 다시 시도</button>}
          {profile && <>
            <div className="cards">
              <div className="stat"><span>분석한 행</span><strong>{profile.row_count.toLocaleString()}</strong></div>
              <div className="stat"><span>컬럼</span><strong>{profile.columns.length}</strong></div>
              <div className="stat"><span>결측 셀</span><strong>{profile.columns.reduce(
                (s, c) => s + c.nulls, 0).toLocaleString()}</strong></div>
            </div>
            <h4>컬럼 프로파일</h4><div className="table-scroll"><table><thead><tr>
              <th>컬럼</th><th>dtype</th><th>NULL</th><th>NaN</th><th>고유 값</th><th>평균</th>
            </tr></thead><tbody>{profile.columns.map(c => <tr key={c.name}>
              <td>{c.name}</td><td>{c.dtype}</td><td>{c.nulls}</td>
              <td>{c.nan_count ?? "—"}</td><td>{c.unique}</td>
              <td>{typeof c.mean === "number" ? c.mean.toFixed(3) : "—"}</td>
            </tr>)}</tbody></table></div>
            <div className="chart-controls"><h4>시계열 / 순서에 따른 데이터</h4>
              <label>값 <select value={visualColumn} onChange={e => setVisualColumn(e.target.value)}>
                {numeric.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select></label>
              <label>가로축 <select value={timeColumn} onChange={e => setTimeColumn(e.target.value)}>
                {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
              </select></label></div>
            <div className="charts">
              <div><h4>값의 변화</h4>{chartOption ? <DataChart option={chartOption} ariaLabel="값의 변화 시계열 차트" /> :
                <p className="muted">표시할 숫자형 데이터가 없습니다.</p>}</div>
              <div><h4>분포 (Histogram)</h4>{distributionOption ? <DataChart option={distributionOption} ariaLabel="데이터 분포 히스토그램" /> :
                <p className="muted">분포를 계산할 숫자형 데이터가 없습니다.</p>}</div>
            </div>
            <p className="hint">차트와 히스토그램은 최대 100행의 미리보기 값입니다.</p>
          </>}
        </section>}
        {tab === "features" && <section className="panel" role="tabpanel"
          id="sos-panel-features" aria-labelledby="sos-tab-features" tabIndex={0}>
          <div className="panel-head"><div><h3>Feature Builder</h3>
            <p>원본은 유지됩니다. 순서대로 변환 단계를 편집·검증하고 전체 Job으로 실행합니다.</p>
          </div></div>
          <div className="form-row">
            <label>변환 <select aria-label="변환" value={op} onChange={e => setOp(e.target.value)}>
              <option value="filter_not_null">결측 행 제외</option>
              <option value="fill_null">결측값 대체</option>
              <option value="cast">Float64 변환</option>
              <option value="lag">Lag 1</option>
              <option value="rolling_mean">Rolling Mean 3</option>
            </select></label>
            <label>컬럼 <select aria-label="컬럼" value={column} onChange={e => setColumn(e.target.value)}>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            {op === "fill_null" && <label>대체 값 <input value={value}
              onChange={e => setValue(e.target.value)} placeholder="0" /></label>}
            {op === "lag" && <label>이전 행 수 <input type="number" min={1} max={1000}
              value={lagPeriods} onChange={e => setLagPeriods(Number(e.target.value))} /></label>}
            {op === "rolling_mean" && <label>윈도 행 수 <input type="number" min={2} max={1000}
              value={rollingWindow} onChange={e => setRollingWindow(Number(e.target.value))} /></label>}
            <button className="secondary" onClick={addStep} disabled={!column || steps.length >= 30 && editingStep === null}>
              {editingStep === null ? "+ 단계 추가" : "단계 수정 반영"}</button>
            {editingStep !== null && <button className="ghost" onClick={() => setEditingStep(null)}>
              수정 취소</button>}
          </div>
          <div className="form-row">
            <label>시간·정렬 컬럼 <select aria-label="시간·정렬 컬럼" value={orderBy} onChange={e => setOrderBy(e.target.value)}>
              <option value="">정렬하지 않음</option>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <label>그룹 컬럼 <select aria-label="그룹 컬럼" value={groupBy} onChange={e => setGroupBy(e.target.value)}>
              <option value="">전체를 하나로 처리</option>
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
                onClick={() => {
                  setSteps(prev => prev.filter((_, j) => j !== i));
                  setEditingStep(null); setFeaturePreview(null);
                }}>삭제</button>
            </div>)}</div>
          <div className="actions"><button disabled={busy || !steps.length} className="secondary"
            onClick={() => void previewFeatures()}>샘플 변환 확인</button>
            {canWrite && <button disabled={busy || !steps.length} onClick={() => void submit("transform")}>
              전체 변환 Job 실행 →
            </button>}</div>
          {featurePreview && <div className="result"><h4>변환 미리보기 · 전체 입력 기준 계산</h4>
             <p className="hint">10만 행 이하 입력에서 실제 Job과 같은 변환을 실행한 뒤 출력 최대 30행을 표시합니다. 더 큰 데이터는 전체 Job을 사용하세요.</p>
            <div className="table-scroll"><table><thead><tr>{Object.entries(featurePreview.schema).map(
              ([c, t]) => <th key={c}>{c}<small>{t}</small></th>
            )}</tr></thead><tbody>{featurePreview.rows.slice(0, 15).map((r, i) => <tr key={i}>
              {Object.keys(featurePreview.schema).map(c => <td key={c}>{r[c] == null ? "—" : String(r[c])}</td>)}
            </tr>)}</tbody></table></div></div>}
        </section>}

      </>}
      {canManage && tab === "storage" && <StoragePanel />}
      {canManage && tab === "audit" && <AuditPanel />}
      {tab === "jobs" && <JobsPanel jobs={[...jobs, ...discoveryJobs].sort((a, b) =>
          b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))}
        datasets={datasets} busy={busy} canCancel={canWrite}
        nextCursor={nextJobsCursor} discoveryNextCursor={nextDiscoveryCursor}
        onLoadMore={() => void loadMoreJobs()}
        onLoadMoreDiscovery={() => void loadMoreDiscoveryJobs()}
        onRefresh={() => void withFeedback(async () => {
          await Promise.all([refreshJobs(), refreshDiscoveryJobs()]);
        })}
        onCancel={id => void cancelJob(id)}
        onOpen={id => void openJobDataset(id)} />}
      <footer>Explore · Experiment · Understand · Evolve <span>개발 환경 · 외부 공개 서비스 아님</span></footer>
    </main>
  </div>;
}
function App() {
  const [actor, setActor] = useState<Identity | null>(null);
  const [checking, setChecking] = useState(true);
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  async function checkIdentity() {
    setChecking(true); setMessage("");
    try {
      setActor(await request<Identity>("/api/auth/me"));
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 401
        ? "인증이 필요합니다. 발급된 액세스 토큰을 입력하세요."
        : `서비스 연결을 확인하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도하세요. ${formatError(error)}`);
    } finally { setChecking(false); }
  }
  useEffect(() => { void checkIdentity(); }, []);
  async function authenticate(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true); setMessage("");
    setAccessToken(token);
    try {
      setActor(await request<Identity>("/api/auth/me"));
      setToken("");
    } catch (error) {
      setAccessToken(null);
      setMessage(formatError(error));
    } finally { setSubmitting(false); }
  }
  if (checking) return <main className="main" role="status">사용자 권한을 확인하고 있습니다.</main>;
  if (!actor) return <main className="main">
    <section className="panel" aria-label="인증">
      <h1>SOS Platform 접근 인증</h1>
      <p>현재 서비스에서 발급받은 Bearer 토큰을 입력하세요.
        토큰은 브라우저 메모리에만 보관하며, 다시 접속하면 재인증이 필요합니다.</p>
      <form onSubmit={event => void authenticate(event)}>
        <label>액세스 토큰 <input type="password" value={token}
          onChange={event => setToken(event.target.value)} autoComplete="off"
          aria-label="액세스 토큰" required /></label>
        <button disabled={submitting || !token}>인증</button>
      </form>
      {message && <p role="alert">{message}</p>}
      <button type="button" className="secondary" onClick={() => void checkIdentity()}>
        연결 다시 확인
      </button>
    </section>
  </main>;
  const canViewDataset = actor.roles.some(r => ["viewer","researcher","administrator"].includes(r));
  return <>
    <div className="auth-status" role="status">
      현재 사용자: {actor.actor_id} · {actor.roles.join(", ")}
      {actor.mode !== "development" &&
        <button className="secondary" onClick={() => { setAccessToken(null); setActor(null); }}>
          인증 해제
        </button>}
    </div>
    {canViewDataset ? <Workspace actor={actor} /> :
      <main className="main"><h1>운영 진단</h1><StoragePanel /><AuditPanel /></main>}
  </>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
