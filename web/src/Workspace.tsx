import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mergeById } from "./collections";
import type * as echarts from "echarts";
import { DataChart, CHART_COLORS } from "./DataChart";
import { AnalysisEvidence, coversAllRows, dataSelectionLabel } from "./AnalysisEvidence";
import { formatDtype } from "./display-format";
import { histogram, needsTimeOrder } from "./feature-utils";
import { useFeatureEditor } from "./useFeatureEditor";
import { FeaturePanel } from "./FeaturePanel";
import { useIcebergConnector } from "./useIcebergConnector";
import { request, jsonPost, submitJob, formatError, uploadFile, describeError, type ErrorNotice } from "./api";
import { ExampleGallery } from "./ExampleGallery";
import { DelimitedImport } from "./DelimitedImport";
import { JobsPanel } from "./JobsPanel";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import { DiscoveryPanel } from "./DiscoveryPanel";
import { UnderstandingPanel } from "./UnderstandingPanel";
import { DatasetQueryPanel, emptyQueryDraft, type QueryDraft } from "./DatasetQueryPanel";
import type { Dataset, DatasetPage, Preview, Job, JobSummary, ExplorationJobSummary, Example, UploadPolicy, Identity } from "./types";
import { WorkspaceTabs, type DatasetTab } from "./WorkspaceTabs";
import { useWorkspaceNavigation, type WorkspaceTab } from "./PlatformNavigation";
import { PlatformShell } from "./PlatformShell";
import { PlatformOverview } from "./PlatformOverview";
import { ActivityWorkspace } from "./ActivityWorkspace";
import { ModelLabUse } from "./ModelLabUse";
import { DataSourceSidebar } from "./DataSourceSidebar";
import { ModelCatalog } from "./ModelCatalog";
import { MLResourceDetail, ScoringWorkspace, DecisionWorkspace } from "./MLResourcePages";
import type { DecisionView } from "./MLDecisionPanel";
import { draftForDataset, type ExplorationDraft } from "./ExplorationDraft";
import { useDatasetOverview } from "./useDatasetOverview";
import { useDatasetInspection } from "./useDatasetInspection";
import { usePagedJobs } from "./usePagedJobs";
import type { ExplorationRequest } from "./types";


type Tab = WorkspaceTab;
type UploadProgress = {
  name: string; loaded: number; total: number;
  phase: "transferring" | "registering";
};
const mebibytes = (size: number) => (size / (1024 * 1024)).toFixed(2);

function readableVersion(d: Dataset) {
  return d.kind === "iceberg" ? `Snapshot ${d.version}` : `SHA256 ${d.version.slice(0, 12)}`;
}
export function Workspace({ actor, onSignOut, brandName, brandReady, brandLoadError,
  onBrandChange, onBrandRetry }: {
  actor: Identity;
  onSignOut: () => void;
  brandName: string;
  brandReady: boolean;
  brandLoadError: string;
  onBrandChange: (name: string) => void;
  onBrandRetry: () => void;
}) {
  // 표시 제어도 서버 /auth/me의 실제 capability 집합을 사용한다.
  // 역할→권한 매핑의 정본은 Python security.GRANTS다.
  const canWrite = actor.capabilities.includes("dataset.create");
  const canRun = actor.capabilities.includes("job.create");
  const canCancel = actor.capabilities.includes("job.cancel");
  const canInspectStorage = actor.capabilities.includes("storage.inspect");
  const canViewAudit = actor.capabilities.includes("audit.read");
  const canConfigure = actor.capabilities.includes("instance.configure");
  const { tab, resource, context, navigate: setTab, navigateWithContext,
    selectContext, openResource } = useWorkspaceNavigation();
  const isDataArea = (["understanding", "preview", "profile", "preparation"] as Tab[]).includes(tab);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetSelectionError, setDatasetSelectionError] = useState("");
  const [datasetSelectionLoading, setDatasetSelectionLoading] = useState(false);
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [nextDatasetCursor, setNextDatasetCursor] = useState<string | null>(null);
  const [rememberedDataset, setSelected] = useState<string>(context.datasetId ?? "");
  const selected = isDataArea ? context.datasetId ?? "" : context.datasetId || rememberedDataset;
  const {
    items: jobs, nextCursor: nextJobsCursor, refresh: refreshJobs, loadMore: loadOlderJobs,
  } = usePagedJobs<JobSummary>("/api/jobs");
  const {
    items: discoveryJobs, nextCursor: nextDiscoveryCursor,
    refresh: refreshDiscoveryJobs, loadMore: loadOlderDiscoveryJobs,
  } = usePagedJobs<ExplorationJobSummary>("/api/explorations/jobs");
  const [examples, setExamples] = useState<Example[]>([]);
  const [examplesLoading, setExamplesLoading] = useState(true);
  const [examplesError, setExamplesError] = useState("");
  const [examplesReload, setExamplesReload] = useState(0);
  const [reloadRevision, setReloadRevision] = useState(0);
  function startScoring(modelId: string) {
    navigateWithContext("scoring", { modelId });
  }
  function startDecision(view: DecisionView, scoringId: string) {
    navigateWithContext(
      view === "policy" ? "policies" : view === "evaluation" ? "evaluations" : "explanations",
      { scoringId, ...(context.modelLabReturn && context.modelId
        ? { modelLabReturn: true, modelId: context.modelId } : {}) },
    );
  }
  const [explorationDraft, setExplorationDraft] = useState<ExplorationDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadPolicy, setUploadPolicy] = useState<UploadPolicy | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [pendingDelimited, setPendingDelimited] = useState<File | null>(null);
  const [error, setErrorNotice] = useState<ErrorNotice | null>(null);
  // 빈 값은 안내를 닫고, 그 외에는 API 오류의 메시지·안내·참조 정보를 나눠 보관한다.
  const setError = useCallback((cause: unknown) =>
    setErrorNotice(cause === "" || cause == null ? null : describeError(cause)), []);
  const [notice, setNotice] = useState("");
  // 화면과 무관한 백그라운드 GET 실패를 전역 작업 실패로 표시하지 않는다.
  const [datasetReadError, setDatasetReadError] = useState("");
  const [jobsReadError, setJobsReadError] = useState("");
  const [discoveryReadError, setDiscoveryReadError] = useState("");
  const [uploadPolicyReadError, setUploadPolicyReadError] = useState("");
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
    useDatasetOverview(isDataArea ? selected : "", reloadRevision);
  const { preview, profile, previewStatus, profileStatus, previewError, profileError } =
    useDatasetInspection(selected, reloadRevision, tab);
  const profileComplete = Boolean(profile?.sampled &&
    coversAllRows(profile.row_count, profile.sample_limit));
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
  const [profileSearch, setProfileSearch] = useState("");
  const [profilePage, setProfilePage] = useState(0);
  const profileColumns = profile?.columns.filter(column =>
    column.name.toLocaleLowerCase().includes(profileSearch.trim().toLocaleLowerCase())) ?? [];
  const currentProfilePage = Math.min(profilePage, Math.max(0, Math.ceil(profileColumns.length / 10) - 1));

  async function refreshDatasets(): Promise<"updated" | "superseded"> {
    const version = ++datasetRequestVersion.current;
    setDatasetsLoading(true);
    let page: DatasetPage;
    try {
      page = await request<DatasetPage>("/api/datasets?limit=20");
    } catch (error) {
      // 새 등록·더 최신 조회에서 대체된 실패는 현재 화면의 오류가 아니다.
      if (version !== datasetRequestVersion.current) return "superseded";
      throw error;
    } finally { if (version === datasetRequestVersion.current) setDatasetsLoading(false); }
    // 오래된 GET 응답이 최근 등록·조회 후 확정된 Dataset 선택을 덮지 못하게 한다.
    if (version !== datasetRequestVersion.current) return "superseded";
    // 이미 조회한 옛 Dataset과 결과 화면의 현재 선택은 최신 20개 밖이어도 보존한다.
    setDatasets(previous => mergeById(page.items, previous));
    setNextDatasetCursor(page.next_cursor ?? null);
    return "updated";
  }
  function chooseDataset(id: string, target: "understanding" | "preview" | "profile" | "preparation") {
    setSelected(id);
    navigateWithContext(target, { datasetId: id });
  }
  function openDataJob(jobId: string, kind: "data" | "exploration" = "data") {
    navigateWithContext("jobs", { datasetId: selected, dataJobId: jobId, dataJobKind: kind });
  }
  /** 서버 등록 성공을 목록 새로고침 실패 때문에 취소된 작업처럼 표시하지 않는다. */
  async function showRegisteredDataset(data: Dataset, success: string, stale: string) {
    setDatasets(previous => [data, ...previous.filter(item => item.id !== data.id)]);
    // 데이터 준비에서 명시적으로 데이터를 등록했다면 Explorer로 강제 이동하지 않는다.
    setSelected(data.id);
    const target = tab === "preparation" ? "preparation" : "understanding";
    chooseDataset(data.id, target);
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
    openDataJob(job.id);
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
  const requestedDatasetLoaded = datasets.some(item => item.id === context.datasetId);
  useEffect(() => {
    if (!isDataArea) return;
    const requestedId = context.datasetId ?? "";
    setSelected(requestedId); setDatasetSelectionError("");
    if (!requestedId || requestedDatasetLoaded) { setDatasetSelectionLoading(false); return; }
    let active = true;
    setDatasetSelectionLoading(true);
    void request<Dataset>(`/api/datasets/${encodeURIComponent(requestedId)}`)
      .then(item => { if (active) setDatasets(previous =>
        [item, ...previous.filter(entry => entry.id !== item.id)]); })
      .catch(e => { if (active) setDatasetSelectionError(formatError(e)); })
      .finally(() => { if (active) setDatasetSelectionLoading(false); });
    return () => { active = false; };
  }, [isDataArea, context.datasetId, requestedDatasetLoaded, reloadRevision]);
  useEffect(() => {
    if (canWrite) {
      request<UploadPolicy>("/api/datasets/upload-policy")
        .then(policy => { setUploadPolicy(policy); setUploadPolicyReadError(""); })
        .catch(e => setUploadPolicyReadError(formatError(e)));
    }
    refreshDatasets().then(() => setDatasetReadError(""))
      .catch(e => setDatasetReadError(formatError(e)));
  }, []);
  async function refreshDataJobs() {
    setJobsLoading(true);
    const results = await Promise.allSettled([refreshJobs(), refreshDiscoveryJobs()]);
    setJobsReadError(results[0].status === "rejected" ? formatError(results[0].reason) : "");
    setDiscoveryReadError(results[1].status === "rejected" ? formatError(results[1].reason) : "");
    setJobsLoading(false);
  }
  useEffect(() => { if (tab === "jobs") void refreshDataJobs(); }, [tab]);
  useEffect(() => {
    let active = true;
    setExamplesLoading(true);
    setExamplesError("");
    void request<Example[]>("/api/examples")
      .then(items => { if (active) setExamples(items); })
      .catch(e => { if (active) { setExamples([]); setExamplesError(formatError(e)); } })
      .finally(() => { if (active) setExamplesLoading(false); });
    return () => { active = false; };
  }, [examplesReload]);
  useEffect(() => {
    // Dataset 전환과 네트워크 재시도를 구분해 작성 중인 변환을 보존한다.
    setVisualColumn(""); setTimeColumn(""); setShowAllPreviewColumns(false); setProfileSearch(""); setProfilePage(0);
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
        refreshJobs().then(() => setJobsReadError(""))
          .catch(e => setJobsReadError(formatError(e)));
      }
      if (discoveryJobs.some(j => !["Succeeded", "Failed", "Cancelled"].includes(j.status))) {
        refreshDiscoveryJobs().then(() => setDiscoveryReadError(""))
          .catch(e => setDiscoveryReadError(formatError(e)));
      }
    }, 2000);
    return () => window.clearInterval(handle);
  }, [jobs, discoveryJobs]);

  // 수동 작업 오류는 다음 화면까지 따라다니지 않는다.
  useEffect(() => { setError(""); }, [tab]);

  async function withFeedback(action: () => Promise<void>) {
    feedbackRequests.current += 1;
    feedbackRevision.current += 1;
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e); }
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
      await showSubmittedJob(job, "작업을 접수했습니다. 실행 상태와 결과를 확인할 수 있습니다.");
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
      setSelected(item.id);
      chooseDataset(item.id, "preview");
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

  return <PlatformShell actor={actor} section={tab} modelContext={context}
    modelResource={resource} onNavigate={setTab}
    onSignOut={onSignOut} canConfigure={canConfigure}
    brandName={brandName} brandReady={brandReady} brandLoadError={brandLoadError}
    onBrandChange={onBrandChange} onBrandRetry={onBrandRetry}
    canInspectStorage={canInspectStorage} canViewAudit={canViewAudit}
    dataSidebar={(["understanding", "preview", "profile", "preparation"] as Tab[]).includes(tab) &&
        <DataSourceSidebar datasets={datasets} loading={datasetsLoading} readError={Boolean(datasetReadError)} selected={selected} canWrite={canWrite} busy={busy}
          uploadRef={uploadRef} uploadPolicy={uploadPolicy}
          onFile={file => {
            if (/\.(tsv|txt)$/i.test(file.name)) {
              setPendingDelimited(file); setError(""); setNotice("");
            } else { void upload(file); }
          }}
          onExample={() => {
            setSelected("");
            navigateWithContext(tab === "preparation" ? "preparation" : "preview", {});
          }}
          onChoose={id => {
            chooseDataset(id, ["preparation", "preview", "profile"].includes(tab)
              ? tab as "preparation" | "preview" | "profile" : "understanding");
          }}
          existingTableName={existingTableName} onChangeExistingName={changeExistingTableName}
          snapshots={snapshots} snapshotId={snapshotId} onSnapshotId={setSnapshotId}
          snapshotsLoading={snapshotsLoading}
          onLoadSnapshots={() => void loadSnapshots()}
          onConnectExisting={() => void connectExisting()}
          hasMore={Boolean(nextDatasetCursor)} onLoadMore={() => void loadMoreDatasets()} />}>
      {error && <div role="alert" className="alert error">
        <div className="alert-body">
          <strong>{error.message}</strong>
          {error.guidance && <p>{error.guidance}</p>}
          {error.reference && <small className="alert-reference">{error.reference}</small>}
        </div>
        <button onClick={() => setError(null)} aria-label="오류 닫기">×</button></div>}
      {datasetReadError && ["overview", "understanding", "preview", "profile", "preparation", "topic"].includes(tab) &&
        <div role="alert" className="alert error">
          Dataset 목록을 확인하지 못했습니다. 현재 화면의 데이터 선택이 제한될 수 있습니다.
          <details><summary>문제 정보</summary>{datasetReadError}</details>
          <button type="button" className="secondary" onClick={() => void refreshDatasets()
            .then(() => setDatasetReadError("")).catch(e => setDatasetReadError(formatError(e)))}>
            Dataset 다시 조회
          </button>
        </div>}
      {jobsReadError && tab === "jobs" &&
        <div role="alert" className="alert error">
          작업 이력을 새로 확인하지 못했습니다. 실행이 실패했다는 의미는 아닙니다.
          <details><summary>문제 정보</summary>{jobsReadError}</details>
          <button type="button" className="secondary" onClick={() => void refreshJobs()
            .then(() => setJobsReadError("")).catch(e => setJobsReadError(formatError(e)))}>
            작업 이력 다시 조회
          </button>
        </div>}
      {discoveryReadError && tab === "jobs" &&
        <div role="alert" className="alert error">
          데이터 탐색 작업 이력을 새로 확인하지 못했습니다.
          <details><summary>문제 정보</summary>{discoveryReadError}</details>
          <button type="button" className="secondary" onClick={() => void refreshDiscoveryJobs()
            .then(() => setDiscoveryReadError("")).catch(e => setDiscoveryReadError(formatError(e)))}>
            탐색 이력 다시 조회
          </button>
        </div>}
      {uploadPolicyReadError && tab === "preparation" &&
        <div role="status" className="alert error">
          업로드 정책을 조회할 수 없어 새 파일 등록이 제한될 수 있습니다.
          <details><summary>문제 정보</summary>{uploadPolicyReadError}</details>
        </div>}
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
      {tab === "overview" && <PlatformOverview brandName={brandName}
        datasets={datasets.map(d => ({ id: d.id, name: d.name, kind: d.kind }))}
        onNavigate={setTab}
        onOpenDataset={id => chooseDataset(id, "understanding")} />}
      {tab === "topic" && <ModelLabUse key="activity-anomaly" datasets={datasets}
          selectedModelId={context.modelId ?? ""}
          onSelectModel={modelId => selectContext({ modelId })}
          hasMoreDatasets={Boolean(nextDatasetCursor)} onLoadMoreDatasets={loadMoreDatasets}
          canRun={canRun} canCancel={canCancel} onOpenResource={openResource}
          canUpload={canWrite} onDatasetRegistered={data => {
            setDatasets(previous => [data, ...previous.filter(item => item.id !== data.id)]);
          }}
          onHistory={() => setTab("ml-jobs")} />}
      {isDataArea && datasets.length > 0 && <div className="dataset-switcher">
        <label>Dataset 선택<select aria-label="현재 Dataset" value={selected}
          onChange={event => chooseDataset(event.target.value, tab as "understanding" | "preview" | "profile" | "preparation")}>
          <option value="">Dataset 선택</option>
          {selected && !dataset && <option value={selected}>{datasetSelectionError ? "선택한 Dataset · 조회 실패" : "선택한 Dataset · 조회 중"}</option>}
          {datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        {nextDatasetCursor && <button type="button" className="secondary" disabled={busy}
          onClick={() => void loadMoreDatasets()}>Dataset 더 보기</button>}
      </div>}
      {isDataArea && selected && !dataset && <section className="panel" aria-label="선택한 Dataset 조회">
        {datasetSelectionError ? <><p role="alert">선택한 Dataset을 열지 못했습니다. {datasetSelectionError}</p>
          <button type="button" className="secondary" onClick={() => setReloadRevision(value => value + 1)}>선택 데이터 다시 조회</button></>
          : <p role="status">{datasetSelectionLoading ? "선택한 Dataset을 불러오는 중입니다." : "선택한 Dataset의 접근 권한을 확인하고 있습니다."}</p>}
      </section>}
      {!selected && isDataArea && <div className="empty">
        <h2>{tab === "preparation" ? "데이터 준비 시작" : "첫 데이터 탐색"}</h2>
        <p>{tab === "preparation"
          ? "Data Preparation은 Explorer 분석 없이도 시작할 수 있습니다. 파일을 가져오거나 기존 Dataset을 선택하세요."
          : "CSV·Parquet·탭 구분 TSV/TXT, Iceberg 테이블 또는 합성 예제로 시작하세요."}</p>
        {canWrite && <button className="secondary" onClick={() => uploadRef.current?.click()}>
          파일 선택 (CSV·Parquet·TSV·TXT)
        </button>}
        <ExampleGallery examples={examples} loading={examplesLoading} busy={busy}
          error={examplesError} onRetry={() => setExamplesReload(v => v + 1)}
          canUse={canWrite} startLabel={tab === "preparation"
            ? "이 예제로 데이터 준비 시작 →" : undefined}
          onUse={id => void selectExample(id)} />
      </div>}
      {dataset && (["understanding", "preview", "profile", "preparation"] as Tab[]).includes(tab) && <>
        <div className="dataset-hero">
          <div><span className="eyebrow">DATASET</span><h2>{dataset.name}</h2>
            <p>{dataset.kind.toUpperCase()} · {readableVersion(dataset)}</p></div>
          <div className="hero-actions">
            <button className="secondary" onClick={() => setReloadRevision(value => value + 1)}>
              데이터 다시 불러오기
            </button>
            <button className="secondary" type="button"
              onClick={() => setTab("ml")}>
              모델 활용 →
            </button>

            {canRun && tab === "preparation" && <>
              <label className="iceberg-append">적재할 Iceberg 테이블
                <input value={importTableName} placeholder="예: sensor_data"
                  onChange={e => setImportTableName(e.target.value)} />
              </label>
              <button disabled={busy || !importTableName.trim()} className="secondary"
                onClick={() => void importIceberg()}>
                Iceberg 테이블에 적재
              </button>
            </>}</div>
        </div>
        {(["understanding", "preview", "profile"] as Tab[]).includes(tab) && <WorkspaceTabs active={tab as DatasetTab} onChange={setTab} />}
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
            onViewJobs={jobId => openDataJob(jobId)}
            overview={overview} overviewStatus={overviewStatus}
            overviewError={overviewError} onRetryOverview={retryOverview} />
        </section>}
        {tab === "preview" && <section className="panel" role="tabpanel"
          id="sos-panel-preview" aria-labelledby="sos-tab-preview" tabIndex={0}>
          <div className="panel-head"><div><h3>데이터 조회</h3><p>최대 100행 · 전체 데이터가 아닌 일부입니다.</p></div>
            <span className="chip">반환 {displayedPreview?.returned_rows ?? "…"}행 · {currentQuery
              ? "조건 일치 전체 건수 미계산"
              : overview?.dataset_id === selected && overview.row_count != null
                ? `전체 ${overview.row_count.toLocaleString("ko-KR")}행`
                : "전체 행 수 미계산"}</span></div>
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
            onCreated={refreshDiscoveryJobs} onOpenJob={id => openDataJob(id, "exploration")} />
          <details className="data-profile-details">
          <summary>컬럼 통계·분포</summary>
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
              summary={(profile.sampled && !profileComplete ? "부분 데이터 분석 · " : "전체 데이터 분석 · ")
                + profile.row_count.toLocaleString() + "행 분석"}
              note={profileComplete
                ? `조회 상한(${(profile.sample_limit ?? 0).toLocaleString()}행)보다 데이터가 작아 전체 행을 분석했습니다.`
                : profile.sampled
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
            <div className="profile-column-tools">
              <h4>컬럼 통계</h4>
              <label>컬럼 검색<input type="search" aria-label="프로파일 컬럼 검색" value={profileSearch}
                onChange={event => { setProfileSearch(event.target.value); setProfilePage(0); }} /></label>
              <span role="status">{profileColumns.length}개 중 {profileColumns.length ? currentProfilePage * 10 + 1 : 0}–{Math.min(profileColumns.length, (currentProfilePage + 1) * 10)}개</span>
            </div>
            <div className="table-scroll"><table><thead><tr>
              <th>컬럼</th><th>dtype</th><th>NULL</th><th>NaN</th><th>고유 값</th><th>평균</th>
            </tr></thead><tbody>{profileColumns.slice(currentProfilePage * 10, (currentProfilePage + 1) * 10).map(c => <tr key={c.name}>
              <td>{c.name}</td><td title={c.dtype}>{formatDtype(c.dtype)}</td><td>{c.nulls}</td>
              <td>{c.nan_count ?? "—"}</td><td>{c.unique}</td>
              <td>{typeof c.mean === "number" ? c.mean.toFixed(3) : "—"}</td>
            </tr>)}</tbody></table></div>
            {profileColumns.length > 10 && <div className="feature-result-pages">
              <button type="button" className="secondary" disabled={currentProfilePage === 0} onClick={() => setProfilePage(currentProfilePage - 1)}>이전 컬럼</button>
              <button type="button" className="secondary" disabled={(currentProfilePage + 1) * 10 >= profileColumns.length} onClick={() => setProfilePage(currentProfilePage + 1)}>다음 컬럼</button>
            </div>}
            <div className="chart-controls"><h4>분포·행 비교</h4>
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
          </details>
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
      {tab === "ml" && !resource && <ModelCatalog
        onOpenModel={id => openResource("model", id)} onScoreModel={startScoring} />}
      {(resource || (["scoring", "policies", "evaluations", "explanations"] as Tab[]).includes(tab)) &&
        context.modelLabReturn && context.modelId &&
        <section className="context-return" role="status">
          <div><strong>활동 이상 탐지에서 열린 결과입니다.</strong>
            <p>선택한 모델과 데이터에 적용한 결과를 살펴본 뒤 모델 적용 화면으로 돌아갈 수 있습니다.</p>
          </div>
          <button type="button" className="secondary"
            onClick={() => navigateWithContext("topic", { modelId: context.modelId })}>
            모델 적용 화면으로 돌아가기 →</button>
        </section>}
      {resource && <MLResourceDetail key={resource.kind + ":" + resource.id}
        kind={resource.kind} id={resource.id} onOpenResource={openResource}
        canRun={canRun} canCancel={canCancel}
        onScoring={startScoring} onDecision={startDecision} />}
      {tab === "scoring" && !resource && <ScoringWorkspace
        datasets={datasets} preferredModelId={context.modelId ?? ""}
        hasMoreDatasets={Boolean(nextDatasetCursor)} onLoadMoreDatasets={loadMoreDatasets}
        canUpload={canWrite} onDatasetRegistered={data => {
          setDatasets(previous => [data, ...previous.filter(item => item.id !== data.id)]);
        }}
        onModelSelected={modelId => selectContext({ ...context, modelId })} onNavigate={setTab}
        canRun={canRun} canCancel={canCancel} onOpenResource={openResource} />}
      {(["policies","evaluations","explanations"] as Tab[]).includes(tab) && !resource &&
        <DecisionWorkspace
          key={tab} view={tab === "policies" ? "policy" : tab === "evaluations" ? "evaluation" : "explanation"}
          preferredScoringId={context.scoringId ?? ""}
          onScoringSelected={scoringId => selectContext({ ...context, scoringId })}
          onNavigate={setTab} canRun={canRun} canCancel={canCancel}
          onOpenResource={openResource} datasets={datasets} />}
      {canInspectStorage && tab === "storage" && <StoragePanel />}
      {canViewAudit && tab === "audit" && <AuditPanel />}
      {(tab === "jobs" || tab === "ml-jobs") && <ActivityWorkspace
        active={tab === "ml-jobs" ? "ml" : "data"}
        onChange={view => setTab(view === "ml" ? "ml-jobs" : "jobs")}
        onNavigate={setTab} onOpenResource={openResource} datasets={datasets}
        canCancel={canCancel}>
        {tab === "jobs" && <JobsPanel jobs={[...jobs, ...discoveryJobs].sort((a, b) =>
          b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))}
          datasets={datasets} busy={busy} canCancel={canCancel}
          selectedJobId={context.dataJobId} selectedJobKind={context.dataJobKind}
          nextCursor={nextJobsCursor} discoveryNextCursor={nextDiscoveryCursor}
          onLoadMore={() => void loadMoreJobs()}
          onLoadMoreDiscovery={() => void loadMoreDiscoveryJobs()}
          onRefresh={() => void refreshDataJobs()} loading={jobsLoading}
          readError={Boolean(jobsReadError || discoveryReadError)}
          onSelectJob={openDataJob}
          onOpen={id => void openJobDataset(id)} />}
      </ActivityWorkspace>}
  </PlatformShell>;
}
