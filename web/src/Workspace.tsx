import React, { useEffect, useMemo, useRef, useState } from "react";
import { mergeById } from "./collections";
import type * as echarts from "echarts";
import { DataChart, CHART_COLORS } from "./DataChart";
import { AnalysisEvidence, dataSelectionLabel } from "./AnalysisEvidence";
import { histogram, needsTimeOrder } from "./feature-utils";
import { useFeatureEditor } from "./useFeatureEditor";
import { FeaturePanel } from "./FeaturePanel";
import { useIcebergConnector } from "./useIcebergConnector";
import { request, jsonPost, submitJob, formatError, uploadFile } from "./api";
import { ExampleGallery } from "./ExampleGallery";
import { DelimitedImport } from "./DelimitedImport";
import { JobsPanel } from "./JobsPanel";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import { DiscoveryPanel } from "./DiscoveryPanel";
import { UnderstandingPanel } from "./UnderstandingPanel";
import { DatasetQueryPanel, emptyQueryDraft, type QueryDraft } from "./DatasetQueryPanel";
import type { Dataset, DatasetPage, Preview, Job, JobSummary, ExplorationJobSummary, Example, UploadPolicy, Identity } from "./types";
import { InstanceBrand } from "./InstanceBrand";
import { WorkspaceTabs, type DatasetTab } from "./WorkspaceTabs";
import { MLWorkbench } from "./MLWorkbench";
import { draftForDataset, type ExplorationDraft } from "./ExplorationDraft";
import { useDatasetOverview } from "./useDatasetOverview";
import { useDatasetInspection } from "./useDatasetInspection";
import { usePagedJobs } from "./usePagedJobs";
import type { ExplorationRequest } from "./types";


type Tab = DatasetTab | "preparation" | "ml" | "jobs" | "storage" | "audit";
const pageLabels: Record<Tab, { eyebrow: string; title: string; description: string }> = {
  understanding: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preview: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  profile: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preparation: { eyebrow: "DATA PREPARATION", title: "데이터 준비", description: "Dataset · Recipe · Transform" },
  ml: { eyebrow: "RESEARCH & ML", title: "ML Workbench", description: "Experiment · Training · Model Revision" },
  jobs: { eyebrow: "ACTIVITY & JOBS", title: "전체 Jobs", description: "Job · Attempt · Result" },
  storage: { eyebrow: "PLATFORM OPERATIONS", title: "저장소 상태", description: "Storage · Health" },
  audit: { eyebrow: "PLATFORM OPERATIONS", title: "보안 감사", description: "Identity · Audit" },
};
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
  const {
    items: jobs, nextCursor: nextJobsCursor, refresh: refreshJobs, loadMore: loadOlderJobs,
  } = usePagedJobs<JobSummary>("/api/jobs");
  const {
    items: discoveryJobs, nextCursor: nextDiscoveryCursor,
    refresh: refreshDiscoveryJobs, loadMore: loadOlderDiscoveryJobs,
  } = usePagedJobs<ExplorationJobSummary>("/api/explorations/jobs");
  const [examples, setExamples] = useState<Example[]>([]);
  const [examplesLoading, setExamplesLoading] = useState(true);
  const [reloadRevision, setReloadRevision] = useState(0);
  const [tab, setTab] = useState<Tab>("understanding");
  const [explorationDraft, setExplorationDraft] = useState<ExplorationDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadPolicy, setUploadPolicy] = useState<UploadPolicy | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [pendingDelimited, setPendingDelimited] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [queryPreview, setQueryPreview] = useState<{
    datasetId: string; revision: number; data: Preview; explicitProjection: boolean
  } | null>(null);
  const [queryDraftState, setQueryDraftState] = useState<{ datasetId: string; draft: QueryDraft } | null>(null);
  const [pendingQuery, setPendingQuery] = useState<{ datasetId: string; revision: number } | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const datasetRequestVersion = useRef(0);
  const feedbackRequests = useRef(0);
  // 늦은 목록 GET 오류가 다음 사용자 작업의 알림을 덮지 않도록 작업 세대를 고정한다.
  const feedbackRevision = useRef(0);
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
  const [showAllPreviewColumns, setShowAllPreviewColumns] = useState(false);
  const currentQuery = queryPreview?.datasetId === selected &&
    queryPreview.revision === reloadRevision ? queryPreview : null;
  const queryDraft = queryDraftState?.datasetId === selected ? queryDraftState.draft : emptyQueryDraft();
  const editedQueryDraft = queryDraft.chosen.length > 0 || !!queryDraft.filterColumn ||
    !!queryDraft.filterValue || !!queryDraft.sortColumn || queryDraft.descending || queryDraft.limit !== 30;
  // Dataset 재조회는 이전 적용 결과를 폐기하지만 편집 중인 조건은 보존한다.
  // 따라서 새 기본 미리보기가 나타나도 해당 조건이 적용되었다고 표시하지 않는다.
  const queryPending = (pendingQuery?.datasetId === selected &&
    pendingQuery.revision === reloadRevision) || (!currentQuery && editedQueryDraft);
  const displayedPreview = currentQuery?.data ?? preview;
  const availableColumns = displayedPreview ? Object.keys(displayedPreview.schema) : [];
  // 사용자가 명시적으로 선택한 투영 컬럼은 임의로 감추지 않는다.
  const displayedColumns = showAllPreviewColumns || currentQuery?.explicitProjection
    ? availableColumns : availableColumns.slice(0, 12);
  const numeric = profile?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const [visualColumn, setVisualColumn] = useState("");
  const [timeColumn, setTimeColumn] = useState("");

  async function refreshDatasets(): Promise<"updated" | "superseded"> {
    const version = ++datasetRequestVersion.current;
    let page: DatasetPage;
    try {
      page = await request<DatasetPage>("/api/datasets?limit=20");
    } catch (error) {
      // 새 등록·더 최신 조회에서 대체된 실패는 현재 화면의 오류가 아니다.
      if (version !== datasetRequestVersion.current) return "superseded";
      throw error;
    }
    // 오래된 GET 응답이 최근 등록·조회 후 확정된 Dataset 선택을 덮지 못하게 한다.
    if (version !== datasetRequestVersion.current) return "superseded";
    // 이미 조회한 옛 Dataset과 결과 화면의 현재 선택은 최신 20개 밖이어도 보존한다.
    setDatasets(previous => mergeById(page.items, previous));
    setNextDatasetCursor(page.next_cursor ?? null);
    setSelected(previous => showExamples.current ? "" : (previous || page.items[0]?.id || ""));
    return "updated";
  }
  /** 서버 등록 성공을 목록 새로고침 실패 때문에 취소된 작업처럼 표시하지 않는다. */
  async function showRegisteredDataset(data: Dataset, success: string, stale: string) {
    setDatasets(previous => [data, ...previous.filter(item => item.id !== data.id)]);
    showExamples.current = false;
    // 데이터 준비에서 명시적으로 데이터를 등록했다면 Explorer로 강제 이동하지 않는다.
    setSelected(data.id); setTab(current => current === "preparation" ? current : "understanding");
    const revision = feedbackRevision.current;
    // HTTP 201이 등록 완료의 정본이며 목록 GET 응답을 기다리지 않고 먼저 안내한다.
    setNotice(success);
    try {
      // 새 목록 GET이 이 요청을 대체해도 201 등록 성공 자체를 취소하지 않는다.
      await refreshDatasets();
    } catch {
      if (revision !== feedbackRevision.current) return;
      // 사용자가 닫았거나 다른 작업이 갱신한 알림은 오래된 GET 실패로 다시 표시하지 않는다.
      setNotice(current => current === success ? stale : current);
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
        `/api/datasets?limit=20&cursor=${encodeURIComponent(nextDatasetCursor)}`);
      if (version !== datasetRequestVersion.current) return;
      setDatasets(previous => mergeById(previous, page.items));
      setNextDatasetCursor(page.next_cursor ?? null);
    });
  }
  async function loadMoreDiscoveryJobs() {
    await withFeedback(loadOlderDiscoveryJobs);
  }
  async function loadMoreJobs() {
    await withFeedback(loadOlderJobs);
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
    setVisualColumn(""); setTimeColumn(""); setShowAllPreviewColumns(false);
    setQueryPreview(null); setQueryDraftState(null); setPendingQuery(null); setExplorationDraft(null);
  }, [selected]);
  useEffect(() => {
    if (!preview) return;
    const columns = Object.keys(preview.schema);
    setColumn(current => columns.includes(current) ? current : (columns[0] ?? ""));
    // 원본의 첫 컬럼이 의미 있는 지표나 시간축이라는 근거는 없다.
    setVisualColumn(current =>
      columns.includes(current) && /Int|Float|Decimal/.test(preview.schema[current])
        ? current : "");
    setTimeColumn(current => columns.includes(current) ? current : "");
  }, [preview]);
  useEffect(() => {
    // 첫 페이지에 없는 과거 성공 Job 때문에 무한히 GET을 반복하지 않는다.
    const unseen = jobs.filter(j => j.status === "Succeeded" && j.result?.dataset_id &&
      !refreshedCompletedJobs.current.has(j.id));
    unseen.forEach(j => refreshedCompletedJobs.current.add(j.id));
    if (unseen.some(j => !datasets.some(d => d.id === j.result?.dataset_id))) {
      // 백그라운드에서 누락된 Job 결과를 목록에 보강하는 best-effort 조회다.
      // 실패를 업로드·Job 제출 실패로 표현하지 않으며 다음 기회에 재조회한다.
      refreshDatasets().catch(() => {
        unseen.forEach(j => refreshedCompletedJobs.current.delete(j.id));
        setNotice(current => current || "일부 Job 결과 Dataset 목록을 갱신하지 못했습니다. 목록을 다시 조회하세요.");
      });
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
    feedbackRevision.current += 1;
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
        "예제는 정상 등록됐습니다. 목록 갱신 결과를 확인하지 못했지만 다시 등록할 필요는 없습니다.");
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
      setError("지원하지 않는 파일 형식입니다. CSV·Parquet 또는 탭으로 구분된 TSV·TXT를 선택하세요.");
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
        "파일은 정상 등록됐습니다. 데이터셋 목록 갱신 결과를 확인하지 못했지만 다시 등록할 필요는 없습니다.");
      // 등록을 확정한 뒤에만 입력 확인 화면을 닫는다. 4xx·네트워크 오류는 수정·재시도 가능해야 한다.
      setPendingDelimited(null);
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
    if (!preview?.rows.length || !visualColumn || !timeColumn) return null;
    const values = preview.rows.map(r => r[visualColumn])
      .filter((n): n is number => typeof n === "number" && Number.isFinite(n));
    // 일정한 값이나 가로축 하나만으로 변화가 존재하는 것처럼 표시하지 않는다.
    if (new Set(values).size < 2 ||
        new Set(preview.rows.map(row => String(row[timeColumn] ?? ""))).size < 2) return null;
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
      <div className="section-title">DATA WORKSPACES</div>
      <button className="job-navigation secondary"
        aria-current={["understanding", "preview", "profile"].includes(tab) ? "page" : undefined}
        onClick={() => setTab("understanding")}>데이터 탐색</button>
      <button className="job-navigation secondary"
        aria-current={tab === "preparation" ? "page" : undefined}
        onClick={() => setTab("preparation")}>데이터 준비</button>
      <div className="section-title">RESEARCH & ML</div>
      <button className="job-navigation secondary"
        aria-current={tab === "ml" ? "page" : undefined}
        onClick={() => setTab("ml")}>ML Workbench</button>
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
      {canWrite && <label className="upload" role="button" tabIndex={0} aria-label="CSV·Parquet·TSV·TXT 파일 업로드"
        aria-disabled={busy} onKeyDown={event => {
          if (busy || !["Enter", " "].includes(event.key)) return;
          event.preventDefault(); uploadRef.current?.click();
        }}>
        + 파일 가져오기 (CSV·Parquet·TSV·TXT)
        {uploadPolicy && <small>최대 {mebibytes(uploadPolicy.max_size_bytes)} MiB</small>}
        <input ref={uploadRef} type="file"
          accept={uploadPolicy?.supported_extensions.join(",") ?? ".csv,.parquet,.tsv,.txt"} disabled={busy}
          onChange={event => {
            const file = event.target.files?.[0];
            if (file && /\.(tsv|txt)$/i.test(file.name)) {
              setPendingDelimited(file); setError(""); setNotice("");
            } else if (file) void upload(file);
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
        setSelected(""); setTab(current => current === "preparation" ? current : "preview");
      }}>예제 데이터 살펴보기</button>
            <div className="section-title dataset-section-title">DATASETS</div>
      <nav className="dataset-nav" aria-label="데이터셋 목록">
        {datasets.length === 0 && <p className="muted">등록된 데이터가 없습니다.</p>}
        {datasets.map(d => <button key={d.id} className={`dataset ${selected === d.id ? "active" : ""}`}
          onClick={() => { showExamples.current = false; setSelected(d.id);
            setTab(current => ["preparation", "preview", "profile"].includes(current)
              ? current : "understanding"); }}>
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
      <header className="workspace-header"><div>
        <span className="eyebrow">{pageLabels[tab].eyebrow}</span>
        <h1>{pageLabels[tab].title}</h1>
        <p>{pageLabels[tab].description}</p>
      </div>
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
      {pendingDelimited && <DelimitedImport file={pendingDelimited} disabled={busy}
        onCancel={() => setPendingDelimited(null)}
        onConfirm={file => { void upload(file); }} />}
      {uploadProgress && <div className="upload-status" role="status" aria-live="polite">
        <strong>{uploadProgress.name}</strong>
        <span>{uploadProgress.phase === "transferring"
          ? `전송 중 · ${mebibytes(uploadProgress.loaded)} / ${mebibytes(uploadProgress.total)} MiB`
          : "전송 완료 · 서버에서 검증하고 Dataset을 등록하고 있습니다."}</span>
        {uploadProgress.phase === "transferring" && uploadProgress.total > 0 &&
          <progress max={uploadProgress.total} value={uploadProgress.loaded}
            aria-label="파일 전송 진행률" />}
      </div>}
      {!dataset && tab !== "jobs" && tab !== "storage" && tab !== "audit" && tab !== "ml" && <div className="empty">
        <h2>{tab === "preparation" ? "데이터 준비 시작" : "첫 데이터를 탐색해 보세요"}</h2>
        <p>{tab === "preparation"
          ? "Data Preparation은 Explorer 분석 없이도 시작할 수 있습니다. 파일을 가져오거나 기존 Dataset을 선택하세요."
          : "CSV·Parquet·탭 구분 TSV/TXT, Iceberg 테이블 또는 합성 예제로 시작하세요."}</p>
        {canWrite && <button className="secondary" onClick={() => uploadRef.current?.click()}>
          파일 선택 (CSV·Parquet·TSV·TXT)
        </button>}
        <ExampleGallery examples={examples} loading={examplesLoading} busy={busy}
          canUse={canWrite} startLabel={tab === "preparation"
            ? "이 예제로 데이터 준비 시작 →" : undefined}
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
            {canRun && tab === "preparation" && <>
              <input aria-label="Iceberg 테이블명" value={importTableName}
                onChange={e => setImportTableName(e.target.value)} placeholder="Iceberg table" />
              <button disabled={busy} className="secondary" onClick={() => void importIceberg()}>
                Iceberg에 추가
              </button>
            </>}</div>
        </div>
        {tab !== "preparation" && <WorkspaceTabs active={tab as DatasetTab} onChange={setTab} />}
        {tab === "understanding" && <section className="panel" role="tabpanel"
          id="sos-panel-understanding" aria-labelledby="sos-tab-understanding" tabIndex={0}>
          <UnderstandingPanel key={selected + ":" + reloadRevision} datasetId={selected}
            canCreate={canRun} onChooseCandidate={selectExplorationCandidate}
            onJobCreated={() => {
              // Job 등록은 이미 성공했다. 목록 갱신 실패를 제출 실패로 표현하지 않는다.
              void refreshJobs().catch(() => setNotice(current => current ||
                "분석 Job은 접수됐습니다. 전체 Jobs에서 목록을 다시 조회할 수 있습니다."
              ));
            }}
            onViewJobs={() => setTab("jobs")}
            overview={overview} overviewStatus={overviewStatus}
            overviewError={overviewError} onRetryOverview={retryOverview} />
        </section>}
        {tab === "preview" && <section className="panel" role="tabpanel"
          id="sos-panel-preview" aria-labelledby="sos-tab-preview" tabIndex={0}>
          <div className="panel-head"><div><h3>데이터 조회</h3><p>최대 100행 · 전체 데이터가 아닌 일부입니다.</p></div>
            <span className="chip">반환 {displayedPreview?.returned_rows ?? "…"}행 · 전체 일치 건수 미계산</span></div>
          {preview && <DatasetQueryPanel key={selected + ":" + reloadRevision} datasetId={selected}
            columns={columnNames} busy={busy} draft={queryDraft}
            onDraftChange={draft => setQueryDraftState({ datasetId: selected, draft })}
            onResult={(data, explicitProjection) => {
              setQueryPreview({ datasetId: selected, revision: reloadRevision, data, explicitProjection });
              setPendingQuery(null);
            }}
            onInvalidate={() => setPendingQuery({ datasetId: selected, revision: reloadRevision })} />}
          {displayedPreview && <p className="query-result-context" role="status">
            {queryPending
              ? currentQuery
                ? "변경 사항 미적용 · 아래 표는 이전에 적용한 조회 결과입니다. 조회 적용을 누르면 새 조건으로 갱신됩니다."
                : "변경 사항 미적용 · 아래 표는 기본 미리보기입니다. 편집 중인 조건은 조회 적용 후 반영됩니다."
              : currentQuery ? "적용된 조회 결과 · 현재 Dataset 판본 기준"
                : "기본 미리보기 · 필터·정렬 조건을 적용하지 않은 결과입니다."}
          </p>}
          {displayedPreview && <>
            {availableColumns.length > 12 && <div className="query-column-summary">
              <span className="hint">전체 {availableColumns.length}개 컬럼 중 {displayedColumns.length}개 표시 · {
                currentQuery?.explicitProjection
                  ? "선택한 컬럼 전부 표시"
                  : "조회한 값의 화면 표시 범위만 변경합니다."
              }</span>
              {!currentQuery?.explicitProjection && <button className="secondary"
                onClick={() => setShowAllPreviewColumns(v => !v)}>
                {showAllPreviewColumns ? "첫 12개 컬럼만 보기" : "전체 컬럼 보기"}
              </button>}
            </div>}
            <div className="table-scroll"><table><thead><tr>
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
          <div className="panel-head"><div><h3>부분 데이터 프로파일</h3>
            <p>정렬 기준 없이 제한 조회한 최대 1,000행에서 계산합니다. 전체 데이터 분석은 별도 Job으로 실행할 수 있습니다.</p>
          </div>{canRun && <button disabled={busy} onClick={() => void submit("profile")}>전체 데이터 프로파일 실행 ↗</button>}</div>
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
            <AnalysisEvidence
              summary={(profile.sampled ? "부분 데이터 분석 · " : "전체 데이터 분석 · ")
                + profile.row_count.toLocaleString() + "행 분석"}
              note={profile.sampled
                ? "정렬 기준 없는 제한 조회 결과입니다. 전체 데이터의 분포를 대표하지 않습니다."
                : "전체 데이터 대상 분석입니다. 개별 수치의 계산 정확도는 별도 확인이 필요합니다."}
              details={[
                { label: "데이터 선택 방식", value: dataSelectionLabel(profile.sampled
                  ? "limited_rows" : "full_scan") },
                { label: "조회 행 수 상한", value: !profile.sampled ? "해당 없음 (전체 대상)"
                  : profile.sample_limit === null ? "미확인" : profile.sample_limit.toLocaleString() + "행" },
                { label: "계산 대상 행 수", value: profile.row_count.toLocaleString() + "행" },
                { label: "고유값 수", value: "조회 범위 내 정확 (NULL 포함)" },
              ]}
            />
            <h4>컬럼 프로파일</h4><div className="table-scroll"><table><thead><tr>
              <th>컬럼</th><th>dtype</th><th>NULL</th><th>NaN</th><th>고유 값</th><th>평균</th>
            </tr></thead><tbody>{profile.columns.map(c => <tr key={c.name}>
              <td>{c.name}</td><td>{c.dtype}</td><td>{c.nulls}</td>
              <td>{c.nan_count ?? "—"}</td><td>{c.unique}</td>
              <td>{typeof c.mean === "number" ? c.mean.toFixed(3) : "—"}</td>
            </tr>)}</tbody></table></div>
            <div className="chart-controls"><h4>조회된 행 순서에 따른 값</h4>
              <label>값 <select aria-label="값 컬럼" value={visualColumn} onChange={e => setVisualColumn(e.target.value)}>
                <option value="">값 컬럼 선택</option>
                {numeric.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select></label>
              <label>가로축 <select aria-label="가로축 컬럼" value={timeColumn} onChange={e => setTimeColumn(e.target.value)}>
                <option value="">가로축 선택</option>
                {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
              </select></label></div>
            <div className="charts">
              <div><h4>행 순서에 따른 값</h4>{chartOption ? <DataChart option={chartOption} ariaLabel="조회 행 순서의 값 차트" /> :
                <p className="muted">값·가로축 컬럼을 선택하면 변화 여부를 확인합니다. 값이나 축이 일정하면 변화 차트를 만들지 않습니다.</p>}</div>
              <div><h4>분포 (Histogram)</h4>{distributionOption ? <DataChart option={distributionOption} ariaLabel="데이터 분포 히스토그램" /> :
                <p className="muted">숫자형 값 컬럼을 선택하면 반환된 행의 분포를 확인합니다.</p>}</div>
            </div>
            <p className="hint">차트와 히스토그램은 최대 100행의 기본 미리보기만 사용합니다. 가로축 값으로 정렬하지 않고 조회된 행 순서로 연결하므로 시간적 추세를 확정하지 않습니다.</p>
          </>}
        </section>}
        {tab === "preparation" && <>
          {!preview && previewStatus === "loading" && <section className="panel">
            <p className="muted" role="status">데이터 준비에 사용할 스키마와 미리보기를 불러오는 중입니다.</p>
          </section>}
          {!preview && previewStatus === "error" && <section className="panel">
            <p role="alert">데이터 준비에 필요한 미리보기를 불러오지 못했습니다. {previewError}</p>
            <button className="secondary" onClick={() => setReloadRevision(v => v + 1)}>
              데이터 준비 미리보기 다시 시도
            </button>
          </section>}
          {preview && <FeaturePanel editor={editor}
            columnNames={columnNames} busy={busy} canRun={canRun}
            onSubmit={() => void submit("transform")} />}
        </>}

      </>}
      {tab === "ml" && <MLWorkbench datasets={datasets} selectedId={selected}
        onSelect={setSelected} canTrain={canRun} canCancel={canCancel} />}
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
      <footer>SOS · {pageLabels[tab].eyebrow} <span>Dataset · Job · Artifact</span></footer>
    </main>
  </div>;
}
