import React, { useEffect, useMemo, useRef, useState } from "react";
import type * as echarts from "echarts";
import { DataChart, CHART_COLORS } from "./DataChart";
import { histogram, needsTimeOrder } from "./feature-utils";
import { useFeatureEditor } from "./useFeatureEditor";
import { FeaturePanel } from "./FeaturePanel";
import { useIcebergConnector } from "./useIcebergConnector";
import { request, jsonPost, submitJob, formatError, uploadFile } from "./api";
import { ExampleGallery } from "./ExampleGallery";
import { JobsPanel } from "./JobsPanel";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import { DiscoveryPanel } from "./DiscoveryPanel";
import { UnderstandingPanel } from "./UnderstandingPanel";
import { DatasetQueryPanel } from "./DatasetQueryPanel";
import type { Dataset, DatasetPage, Preview, Job, JobSummary, JobPage, ExplorationJobSummary, ExplorationJobPage, Example, UploadPolicy, Identity } from "./types";
import { InstanceBrand } from "./InstanceBrand";
import { WorkspaceTabs, type DatasetTab } from "./WorkspaceTabs";
import { draftForDataset, type ExplorationDraft } from "./ExplorationDraft";
import { useDatasetOverview } from "./useDatasetOverview";
import { useDatasetInspection } from "./useDatasetInspection";
import type { ExplorationRequest } from "./types";


type Tab = DatasetTab | "jobs" | "storage" | "audit";
type UploadProgress = {
  name: string; loaded: number; total: number;
  phase: "transferring" | "registering";
};
const mebibytes = (size: number) => (size / (1024 * 1024)).toFixed(2);

function readableVersion(d: Dataset) {
  return d.kind === "iceberg" ? `Snapshot ${d.version}` : `SHA256 ${d.version.slice(0, 12)}`;
}
export function Workspace({ actor, onSignOut }: { actor: Identity; onSignOut: () => void }) {
  // 표시 제어도 서버 /auth/me의 실제 capability 집합을 사용한다.
  // 역할→권한 매핑의 정본은 Python security.GRANTS다.
  const canWrite = actor.capabilities.includes("dataset.create");
  const canRun = actor.capabilities.includes("job.create");
  const canCancel = actor.capabilities.includes("job.cancel");
  const canInspectStorage = actor.capabilities.includes("storage.inspect");
  const canViewAudit = actor.capabilities.includes("audit.read");
  const canConfigure = actor.capabilities.includes("instance.configure");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [nextDatasetCursor, setNextDatasetCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [nextJobsCursor, setNextJobsCursor] = useState<string | null>(null);
  const [discoveryJobs, setDiscoveryJobs] = useState<ExplorationJobSummary[]>([]);
  const [nextDiscoveryCursor, setNextDiscoveryCursor] = useState<string | null>(null);
  const [olderDiscoveryJobsLoaded, setOlderDiscoveryJobsLoaded] = useState(false);
  const [olderJobsLoaded, setOlderJobsLoaded] = useState(false);
  const [examples, setExamples] = useState<Example[]>([]);
  const [examplesLoading, setExamplesLoading] = useState(true);
  const [reloadRevision, setReloadRevision] = useState(0);
  const [tab, setTab] = useState<Tab>("understanding");
  const [explorationDraft, setExplorationDraft] = useState<ExplorationDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadPolicy, setUploadPolicy] = useState<UploadPolicy | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [queryPreview, setQueryPreview] = useState<{
    datasetId: string; revision: number; data: Preview
  } | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const datasetRequestVersion = useRef(0);
  const jobsRequestVersion = useRef(0);
  const discoveryJobsRequestVersion = useRef(0);
  const feedbackRequests = useRef(0);
  const showExamples = useRef(false);
  const refreshedCompletedJobs = useRef(new Set<string>());
  const dataset = datasets.find(d => d.id === selected);
  const activeExplorationDraft = dataset
    ? draftForDataset(explorationDraft, dataset.id, dataset.version) : null;
  function changeExploration(request: ExplorationRequest) {
    if (!dataset) return;
    setExplorationDraft(current => ({
      datasetId: dataset.id,
      version: dataset.version,
      request,
      source: draftForDataset(current, dataset.id, dataset.version)?.source,
    }));
  }
  function selectExplorationCandidate(
    candidate: { request: ExplorationRequest; title: string; reason: string },
    sourceVersion: string, profileScope: "sample" | "full",
  ) {
    if (!dataset || dataset.version !== sourceVersion) {
      setError("탐색 후보의 데이터 판본과 현재 Dataset이 다릅니다. 개요를 새로고침하세요.");
      return;
    }
    setExplorationDraft({
      datasetId: dataset.id,
      version: dataset.version,
      request: candidate.request,
      source: { title: candidate.title, reason: candidate.reason, profileScope },
    });
    setTab("profile");
    requestAnimationFrame(() => document.getElementById("sos-tab-profile")?.focus());
  }
  const { overview, overviewStatus, overviewError, retryOverview } =
    useDatasetOverview(selected, reloadRevision);
  const { preview, profile, previewStatus, profileStatus, previewError, profileError } =
    useDatasetInspection(selected, reloadRevision, tab);
  const columnNames = preview ? Object.keys(preview.schema) : [];
  const editor = useFeatureEditor({
    datasetId: selected, revision: reloadRevision, preview, onError: setError, withFeedback,
  });
  const { steps, orderBy, transform, setColumn } = editor;
  const {
    existingTableName, changeExistingTableName, importTableName, setImportTableName,
    snapshots, snapshotId, setSnapshotId, snapshotsLoading,
    loadSnapshots, connectExisting, importIceberg,
  } = useIcebergConnector({
    selected, onError: setError, withFeedback,
    onConnected: showRegisteredDataset, onImported: showSubmittedJob,
  });
  const displayedPreview = queryPreview?.datasetId === selected &&
    queryPreview.revision === reloadRevision ? queryPreview.data : preview;
  const displayedColumns = displayedPreview ? Object.keys(displayedPreview.schema) : [];
  const numeric = profile?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const [visualColumn, setVisualColumn] = useState("");
  const [timeColumn, setTimeColumn] = useState("");

  async function refreshDatasets() {
    const version = ++datasetRequestVersion.current;
    const page = await request<DatasetPage>("/api/datasets/pages?limit=20");
    // 오래된 GET 응답이 최근 등록·조회 후 확정된 Dataset 선택을 덮지 못하게 한다.
    if (version !== datasetRequestVersion.current) return;
    // 이미 조회한 옛 Dataset과 결과 화면의 현재 선택은 최신 20개 밖이어도 보존한다.
    setDatasets(previous => [
      ...page.items,
      ...previous.filter(item => !page.items.some(d => d.id === item.id)),
    ]);
    setNextDatasetCursor(page.next_cursor ?? null);
    setSelected(previous => showExamples.current ? "" : (previous || page.items[0]?.id || ""));
  }
  /** 서버 등록 성공을 목록 새로고침 실패 때문에 취소된 작업처럼 표시하지 않는다. */
  async function showRegisteredDataset(data: Dataset, success: string, stale: string) {
    setDatasets(previous => [data, ...previous.filter(item => item.id !== data.id)]);
    showExamples.current = false;
    setSelected(data.id); setTab("understanding");
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
  async function loadMoreDatasets() {
    if (!nextDatasetCursor) return;
    await withFeedback(async () => {
      const version = datasetRequestVersion.current;
      const page = await request<DatasetPage>(
        `/api/datasets/pages?limit=20&cursor=${encodeURIComponent(nextDatasetCursor)}`);
      if (version !== datasetRequestVersion.current) return;
      setDatasets(previous => [...previous, ...page.items.filter(
        item => !previous.some(d => d.id === item.id))]);
      setNextDatasetCursor(page.next_cursor ?? null);
    });
  }
  async function refreshJobs() {
    const version = ++jobsRequestVersion.current;
    const page = await request<JobPage>("/api/jobs?limit=20");
    // 폴링과 수동 갱신이 겹쳐도 더 오래된 응답으로 Job 상태를 되돌리지 않는다.
    if (version !== jobsRequestVersion.current) return;
    setJobs(previous => olderJobsLoaded
      ? [...page.items, ...previous.filter(job => !page.items.some(item => item.id === job.id))]
      : page.items);
    if (!olderJobsLoaded) setNextJobsCursor(page.next_cursor ?? null);
  }
  async function refreshDiscoveryJobs() {
    const version = ++discoveryJobsRequestVersion.current;
    const page = await request<ExplorationJobPage>("/api/explorations/jobs?limit=20");
    if (version !== discoveryJobsRequestVersion.current) return;
    setDiscoveryJobs(previous => olderDiscoveryJobsLoaded
      ? [...page.items, ...previous.filter(job => !page.items.some(item => item.id === job.id))]
      : page.items);
    if (!olderDiscoveryJobsLoaded) setNextDiscoveryCursor(page.next_cursor ?? null);
  }
  async function loadMoreDiscoveryJobs() {
    if (!nextDiscoveryCursor) return;
    await withFeedback(async () => {
      const version = discoveryJobsRequestVersion.current;
      const page = await request<ExplorationJobPage>(
        `/api/explorations/jobs?limit=20&cursor=${encodeURIComponent(nextDiscoveryCursor)}`);
      if (version !== discoveryJobsRequestVersion.current) return;
      setDiscoveryJobs(previous => [...previous, ...page.items.filter(
        item => !previous.some(job => job.id === item.id))]);
      setNextDiscoveryCursor(page.next_cursor ?? null);
      setOlderDiscoveryJobsLoaded(true);
    });
  }
  async function loadMoreJobs() {
    if (!nextJobsCursor) return;
    await withFeedback(async () => {
      const version = jobsRequestVersion.current;
      const page = await request<JobPage>(
        `/api/jobs?limit=20&cursor=${encodeURIComponent(nextJobsCursor)}`);
      if (version !== jobsRequestVersion.current) return;
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
    refreshDatasets().catch(e => setError(formatError(e)));
    refreshJobs().catch(e => setError(formatError(e)));
    refreshDiscoveryJobs().catch(e => setError(formatError(e)));
    request<Example[]>("/api/examples").then(setExamples)
      .catch(e => setError(formatError(e)))
      .finally(() => setExamplesLoading(false));
  }, []);
  useEffect(() => {
    // Dataset 전환과 네트워크 재시도를 구분해 작성 중인 변환을 보존한다.
    setVisualColumn(""); setTimeColumn("");
    setQueryPreview(null); setExplorationDraft(null);
  }, [selected]);
  useEffect(() => {
    if (!preview) return;
    const columns = Object.keys(preview.schema);
    setColumn(current => columns.includes(current) ? current : (columns[0] ?? ""));
    setVisualColumn(current =>
      columns.includes(current) && /Int|Float|Decimal/.test(preview.schema[current])
        ? current : (columns.find(name => /Int|Float|Decimal/.test(preview.schema[name]))
          ?? columns[0] ?? ""));
    setTimeColumn(current => columns.includes(current) ? current : (columns[0] ?? ""));
  }, [preview]);
  useEffect(() => {
    // 첫 페이지에 없는 과거 성공 Job 때문에 무한히 GET을 반복하지 않는다.
    const unseen = jobs.filter(j => j.status === "Succeeded" && j.result?.dataset_id &&
      !refreshedCompletedJobs.current.has(j.id));
    unseen.forEach(j => refreshedCompletedJobs.current.add(j.id));
    if (unseen.some(j => !datasets.some(d => d.id === j.result?.dataset_id))) {
      refreshDatasets().catch(e => setError(formatError(e)));
    }
  }, [jobs, datasets]);
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (jobs.some(j => !["Succeeded", "Failed", "Cancelled"].includes(j.status))) {
        refreshJobs().catch(e => setError(formatError(e)));
      }
      if (discoveryJobs.some(j => !["Succeeded", "Failed", "Cancelled"].includes(j.status))) {
        refreshDiscoveryJobs().catch(e => setError(formatError(e)));
      }
    }, 2000);
    return () => window.clearInterval(handle);
  }, [jobs, discoveryJobs]);

  async function withFeedback(action: () => Promise<void>) {
    feedbackRequests.current += 1;
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(formatError(e)); }
    finally {
      feedbackRequests.current -= 1;
      setBusy(feedbackRequests.current > 0);
    }
  }
  async function submit(kind: "profile" | "transform") {
    if (!selected) return;
    if (kind === "transform" && needsTimeOrder(steps) && !orderBy) {
      setError("Lag와 Rolling 변환에는 정렬 컬럼을 지정해야 합니다."); return;
    }
    await withFeedback(async () => {
      const job = await submitJob<Job>("/api/jobs",
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
    await withFeedback(async () => {
      // 완료 Job의 출력은 최신 Dataset 첫 페이지 밖에 있을 수 있다.
      const item = await request<Dataset>(`/api/datasets/${encodeURIComponent(datasetId)}`);
      setDatasets(previous => [item, ...previous.filter(d => d.id !== item.id)]);
      showExamples.current = false;
      setSelected(item.id); setTab("preview");
    });
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
      <InstanceBrand canEdit={canConfigure} />
      <nav className="global-nav" aria-label="전역 메뉴">
      <div className="section-title">PLATFORM</div>
      <button className="job-navigation secondary" aria-current={tab === "jobs" ? "page" : undefined}
        onClick={() => setTab("jobs")}>
        전체 Jobs 보기 {jobs.length > 0 ? `· ${jobs.length}` : ""}
      </button>
      {canInspectStorage && <button className="job-navigation secondary" aria-current={tab === "storage" ? "page" : undefined}
        onClick={() => setTab("storage")}>
        저장소 상태 보기
      </button>}
      {canViewAudit && <button className="job-navigation secondary" aria-current={tab === "audit" ? "page" : undefined}
        onClick={() => setTab("audit")}>
        보안 감사 보기
      </button>}
      </nav>

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
          <input aria-label="Iceberg 테이블 이름" value={existingTableName}
            onChange={e => changeExistingTableName(e.target.value)}
            placeholder="sensor_data" />
        </label>
        <button className="secondary" disabled={busy || snapshotsLoading || !existingTableName.trim()}
          onClick={() => void loadSnapshots()}>
          {snapshotsLoading ? "조회 중" : "Snapshot 조회"}
        </button>
        {snapshots.length > 0 && <label>사용할 Snapshot
          <select aria-label="Iceberg Snapshot 선택" value={snapshotId}
            onChange={event => setSnapshotId(event.target.value)}>
            {snapshots.map(item => <option key={item.snapshot_id} value={item.snapshot_id}>
              {item.snapshot_id} · {new Date(item.timestamp_ms).toLocaleString("ko-KR")}
            </option>)}
          </select>
        </label>}
        <button className="secondary" disabled={busy || !existingTableName.trim()}
          onClick={() => void connectExisting()}>
          {snapshotId ? "선택한 Snapshot 연결" : "최신 Snapshot 연결"}
        </button>
      </div>}
      <button className="job-navigation secondary" onClick={() => {
        showExamples.current = true;
        setSelected(""); setTab("preview");
      }}>예제 데이터 살펴보기</button>
            <div className="section-title dataset-section-title">DATASETS</div>
      <nav className="dataset-nav" aria-label="데이터셋 목록">
        {datasets.length === 0 && <p className="muted">등록된 데이터가 없습니다.</p>}
        {datasets.map(d => <button key={d.id} className={`dataset ${selected === d.id ? "active" : ""}`}
          onClick={() => { showExamples.current = false; setSelected(d.id); setTab("understanding"); }}>
          <span className="dataset-name">{d.name}</span>
          <span className="dataset-kind">{d.kind === "iceberg" ? "◆ Iceberg" : "◫ File"}</span>
        </button>)}
      </nav>
      {nextDatasetCursor && <button className="job-navigation secondary"
        disabled={busy} onClick={() => void loadMoreDatasets()}>
        이전 데이터셋 더 불러오기
      </button>}
      <div className="sidebar-bottom"><a href="/docs" target="_blank" rel="noreferrer">↗ Scalar API Reference</a></div>
    </aside>
    <main className="main">
      <header className="workspace-header"><div><span className="eyebrow">DATA EXPLORER</span>
        <h1>데이터 탐색</h1>
        <p>Dataset · Profile · Exploration</p></div>
        <div className="header-meta">
          <span className="environment">{actor.mode === "development" ? "로컬 인증" : "토큰 인증"}</span>
          <div className="auth-status">
            <span>{actor.actor_id} · {actor.roles.join(", ")}</span>
            {actor.mode !== "development" &&
              <button type="button" className="ghost" onClick={onSignOut}>인증 해제</button>}
          </div>
        </div>
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
          <div><span className="eyebrow">DATASET</span><h2>{dataset.name}</h2>
            <p>{dataset.kind.toUpperCase()} · {readableVersion(dataset)}</p></div>
          <div className="hero-actions">
            <button className="secondary" onClick={() => setReloadRevision(value => value + 1)}>
              데이터 다시 불러오기
            </button>
            {canRun && <>
              <input aria-label="Iceberg 테이블명" value={importTableName}
                onChange={e => setImportTableName(e.target.value)} placeholder="Iceberg table" />
              <button disabled={busy} className="secondary" onClick={() => void importIceberg()}>
                Iceberg에 추가
              </button>
            </>}</div>
        </div>
        <WorkspaceTabs active={tab as DatasetTab} onChange={setTab} />
        {tab === "understanding" && <section className="panel" role="tabpanel"
          id="sos-panel-understanding" aria-labelledby="sos-tab-understanding" tabIndex={0}>
          <UnderstandingPanel key={selected + ":" + reloadRevision} datasetId={selected}
            canCreate={canRun} onChooseCandidate={selectExplorationCandidate}
            overview={overview} overviewStatus={overviewStatus}
            overviewError={overviewError} onRetryOverview={retryOverview} />
        </section>}
        {tab === "preview" && <section className="panel" role="tabpanel"
          id="sos-panel-preview" aria-labelledby="sos-tab-preview" tabIndex={0}>
          <div className="panel-head"><div><h3>데이터 조회</h3><p>최대 100행 · 전체 데이터가 아닌 일부입니다.</p></div>
            <span className="chip">{displayedPreview?.returned_rows ?? "…"} rows shown</span></div>
          {preview && <DatasetQueryPanel key={selected + ":" + reloadRevision} datasetId={selected}
            columns={columnNames} busy={busy}
            onResult={data => setQueryPreview({ datasetId: selected, revision: reloadRevision, data })}
            onInvalidate={() => setQueryPreview(null)} />}
          {displayedPreview && <><div className="table-scroll"><table><thead><tr>
            {displayedColumns.map(c => <th key={c}>{c}<small>{displayedPreview.schema[c]}</small></th>)}
          </tr></thead><tbody>{displayedPreview.rows.map((r, i) => <tr key={i}>
            {displayedColumns.map(c => <td key={c}>{r[c] == null
              ? <span className="null">NULL</span> : String(r[c])}</td>)}
          </tr>)}</tbody></table></div>{displayedPreview.rows.length === 0 && <p className="muted">조건에 맞는 행이 없습니다.</p>}</>}
          {previewStatus === "loading" && <p className="muted" role="status">미리보기를 불러오는 중입니다.</p>}
          {previewStatus === "error" && <p role="alert">{previewError}</p>}
          {previewStatus === "error" && <button className="secondary"
            onClick={() => setReloadRevision(v => v + 1)}>미리보기 다시 시도</button>}
          <div className="hint">스키마와 파일·스냅샷 버전은 실행 입력으로 고정됩니다.</div>
        </section>}
        {tab === "profile" && <section className="panel" role="tabpanel"
          id="sos-panel-profile" aria-labelledby="sos-tab-profile" tabIndex={0}>
          <DiscoveryPanel key={selected + ":" + reloadRevision} datasetId={selected}
            canCreate={canRun} draft={activeExplorationDraft} onDraftChange={changeExploration}
            overview={overview} overviewStatus={overviewStatus}
            overviewError={overviewError} onRetryOverview={retryOverview}
            onCreated={async () => { await refreshDiscoveryJobs(); setTab("jobs"); }} />
          <div className="panel-head"><div><h3>샘플 프로파일</h3>
            <p>미리보기 통계는 상위 1,000행 기준입니다. 전체 분석은 Job으로 요청할 수 있습니다.</p>
          </div>{canRun && <button disabled={busy} onClick={() => void submit("profile")}>전체 Profile 실행 ↗</button>}</div>
          {profileStatus === "loading" && <p role="status" className="muted">EDA 통계를 계산하고 있습니다.</p>}
          {profileStatus === "error" && <p role="alert">{profileError}</p>}
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
        {tab === "features" && <FeaturePanel editor={editor}
          columnNames={columnNames} busy={busy} canRun={canRun}
          onSubmit={() => void submit("transform")} />}

      </>}
      {canInspectStorage && tab === "storage" && <StoragePanel />}
      {canViewAudit && tab === "audit" && <AuditPanel />}
      {tab === "jobs" && <JobsPanel jobs={[...jobs, ...discoveryJobs].sort((a, b) =>
          b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))}
        datasets={datasets} busy={busy} canCancel={canCancel}
        nextCursor={nextJobsCursor} discoveryNextCursor={nextDiscoveryCursor}
        onLoadMore={() => void loadMoreJobs()}
        onLoadMoreDiscovery={() => void loadMoreDiscoveryJobs()}
        onRefresh={() => void withFeedback(async () => {
          await Promise.all([refreshJobs(), refreshDiscoveryJobs()]);
        })}
        onCancel={id => void cancelJob(id)}
        onOpen={id => void openJobDataset(id)} />}
      <footer>SOS · Data Explorer <span>Dataset · Job · Artifact</span></footer>
    </main>
  </div>;
}
