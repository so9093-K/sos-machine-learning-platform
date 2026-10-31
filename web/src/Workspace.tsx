import { useCallback, useEffect, useRef, useState } from "react";
import { needsTimeOrder } from "./feature-utils";
import { useFeatureEditor } from "./useFeatureEditor";
import { FeaturePanel } from "./FeaturePanel";
import { useIcebergConnector } from "./useIcebergConnector";
import { request, jsonPost, submitJob, describeError, type ErrorNotice } from "./api";
import { ErrorNotice as ErrorNoticeView } from "./ErrorNotice";
import { AvailableExamples } from "./ExampleGallery";
import { DelimitedImport } from "./DelimitedImport";
import { JobsPanel } from "./JobsPanel";
import { StoragePanel } from "./StoragePanel";
import { ServingOperations } from "./ServingOperations";
import { AuditPanel } from "./AuditPanel";
import { DiscoveryPanel } from "./DiscoveryPanel";
import { UnderstandingPanel } from "./UnderstandingPanel";
import type { AnalysisTopic, Dataset, Job, Identity, ExplorationRequest, ModelRevision } from "./types";
import { WorkspaceTabs, type DatasetTab } from "./WorkspaceTabs";
import { useWorkspaceNavigation, type WorkspaceTab } from "./PlatformNavigation";
import { PlatformShell } from "./PlatformShell";
import { PlatformOverview } from "./PlatformOverview";
import { ActivityWorkspace } from "./ActivityWorkspace";
import { ActivityPlayground } from "./ActivityPlayground";
import { AnalysisTaskPage, LearningTypePage, learningTypeGuides, playgroundTopicFor, taskGuides,
  useTopicCatalog, type TaxonomyLinks } from "./ModelLabGuide";
import { PipelineWorkspace } from "./PipelineWorkspace";
import { DataSourceBar } from "./DataSourceBar";
import { ModelCatalog } from "./ModelCatalog";
import { MLResourceDetail, ScoringWorkspace, DecisionWorkspace } from "./MLResourcePages";
import { draftForDataset, type ExplorationDraft } from "./ExplorationDraft";
import { useDatasetOverview } from "./useDatasetOverview";
import { useDatasetInspection } from "./useDatasetInspection";
import { useDatasetCatalog, useRequestedDataset } from "./useDatasetCatalog";
import { useDataJobs } from "./useDataJobs";
import { UploadProgressStatus, useDatasetUpload } from "./DatasetUpload";
import { DatasetPreviewPanel, useDatasetQuery } from "./DatasetPreviewTab";
import { ProfileStatistics, useProfileSelection } from "./ProfileStatistics";

type Tab = WorkspaceTab;
const DATA_TABS: Tab[] = ["understanding", "preview", "profile", "preparation"];

/** 화면 이동(URL)·공유 Dataset 목록·전역 안내를 소유하고 업무 화면을 조합한다.
 *
 *  화면 안에서만 쓰는 편집·조회 상태는 각 화면 컴포넌트가 가진다. 데이터 영역의 조회 조건과
 *  탐색 초안은 탭을 오가도 유지해야 하므로 여기서 Dataset 단위로 보관한다.
 */
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
  // 등록 파이프라인은 학습 권한 계정이 모델을 공급하는 운영 도구다. 메뉴·새 실행은 학습 권한,
  // 기존 실행 결과의 직접 링크 열람은 API와 같이 작업 조회 권한을 따른다.
  const canOperatePipelines = actor.capabilities.includes("model.train") && actor.capabilities.includes("dataset.read");
  const canViewPipelineRuns = actor.capabilities.includes("job.read");
  const { tab, resource, context, navigate: setTab, navigateWithContext,
    selectContext, openResource } = useWorkspaceNavigation();
  const isDataArea = DATA_TABS.includes(tab);
  const catalog = useDatasetCatalog();
  const { datasets } = catalog;
  const [rememberedDataset, setSelected] = useState<string>(context.datasetId ?? "");
  const selected = isDataArea ? context.datasetId ?? "" : context.datasetId || rememberedDataset;
  const dataJobs = useDataJobs(tab === "jobs");
  const [reloadRevision, setReloadRevision] = useState(0);
  const selectionActive = isDataArea || tab === "pipelines";
  const requested = useRequestedDataset(context.datasetId ?? "",
    datasets.some(item => item.id === context.datasetId), selectionActive,
    `${tab}:${reloadRevision}`, catalog.upsert);
  useEffect(() => {
    if (selectionActive) setSelected(context.datasetId ?? "");
  }, [selectionActive, tab, context.datasetId]);
  const taxonomyLinks: TaxonomyLinks = {
    onOpenLearningType: id => navigateWithContext("learning", { taxonomyId: id }),
    onOpenTask: id => navigateWithContext("task", { taxonomyId: id }),
    onOpenTopic: () => setTab("topic"),
  };
  const topicCatalog = useTopicCatalog(actor.capabilities.includes("job.read"));
  // 체험 주제가 있는 모델은 그 Playground에서, 그 밖의 모델은 공통 모델 적용 화면에서 적용한다.
  // 체험 주제 판단에는 카탈로그가 필요하다. 조회 전이면 기다리고, 실패하면 다른 화면으로 보내지 않는다.
  async function applyModel(model: ModelRevision) {
    let topics = topicCatalog.items;
    if (!topics) {
      try {
        topics = await request<AnalysisTopic[]>("/api/topics");
      } catch (cause) {
        const notice = describeError(cause);
        setErrorNotice({ ...notice, message: "모델을 적용할 화면을 확인하지 못했습니다. 다시 시도하세요.",
          guidance: notice.message });
        return;
      }
    }
    if (playgroundTopicFor(model, topics) === "activity_anomaly") {
      navigateWithContext("topic", { modelId: model.id, datasetId: context.datasetId });
    } else startScoring(model.id);
  }
  function startScoring(modelId: string) {
    navigateWithContext("scoring", { modelId, datasetId: context.datasetId, pipelineRunId: context.pipelineRunId });
  }
  const [explorationDraft, setExplorationDraft] = useState<ExplorationDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setErrorNotice] = useState<ErrorNotice | null>(null);
  // 빈 값은 안내를 닫고, 그 외에는 API 오류의 메시지·안내·참조 정보를 나눠 보관한다.
  const setError = useCallback((cause: unknown) =>
    setErrorNotice(cause === "" || cause == null ? null : describeError(cause)), []);
  const [notice, setNotice] = useState("");
  const uploadRef = useRef<HTMLInputElement>(null);
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
  const query = useDatasetQuery(selected, reloadRevision);
  const profileSelection = useProfileSelection(selected);
  const uploads = useDatasetUpload({
    canWrite, withFeedback,
    onRejected: message => { setNotice(""); setError(message); },
    onRegistered: data => showRegisteredDataset(data,
      "파일 등록이 완료됐습니다. 데이터 미리보기와 분석을 시작할 수 있습니다.",
      "파일은 정상 등록됐습니다. 데이터셋 목록 갱신 결과를 확인하지 못했지만 다시 등록할 필요는 없습니다."),
  });

  function chooseDataset(id: string, target: "understanding" | "preview" | "profile" | "preparation") {
    setSelected(id);
    navigateWithContext(target, { datasetId: id });
  }
  function openDataJob(jobId: string, kind: "data" | "exploration" = "data") {
    navigateWithContext("jobs", { datasetId: selected, dataJobId: jobId, dataJobKind: kind });
  }
  /** 서버 등록 성공을 목록 새로고침 실패 때문에 취소된 작업처럼 표시하지 않는다. */
  async function showRegisteredDataset(data: Dataset, success: string, stale: string) {
    catalog.upsert(data);
    // 데이터 준비에서 명시적으로 데이터를 등록했다면 Explorer로 강제 이동하지 않는다.
    setSelected(data.id);
    const target = tab === "preparation" ? "preparation" : "understanding";
    chooseDataset(data.id, target);
    const revision = feedbackRevision.current;
    // HTTP 201이 등록 완료의 정본이며 목록 GET 응답을 기다리지 않고 먼저 안내한다.
    setNotice(success);
    try {
      // 새 목록 GET이 이 요청을 대체해도 201 등록 성공 자체를 취소하지 않는다.
      await catalog.refresh();
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
      await dataJobs.refreshJobs();
      setNotice(message);
    } catch {
      setError("");
      setNotice(`Job은 정상 접수됐습니다. 목록을 다시 확인하세요. 작업 ID: ${job.id}`);
    }
  }
  async function loadMoreDatasets() {
    await withFeedback(catalog.loadMore);
  }
  async function loadMoreDiscoveryJobs() {
    await withFeedback(dataJobs.loadOlderExplorations);
  }
  async function loadMoreJobs() {
    await withFeedback(dataJobs.loadOlderJobs);
  }
  // 탐색 초안은 Dataset 판본에 귀속한다. 다른 Dataset의 조건을 이어받지 않는다.
  useEffect(() => { setExplorationDraft(null); }, [selected]);
  useEffect(() => {
    if (!preview) return;
    const columns = Object.keys(preview.schema);
    setColumn(current => columns.includes(current) ? current : (columns[0] ?? ""));
  }, [preview]);
  useEffect(() => {
    // 첫 페이지에 없는 과거 성공 Job 때문에 무한히 GET을 반복하지 않는다.
    const unseen = dataJobs.jobs.filter(j => j.status === "Succeeded" && j.result?.dataset_id &&
      !refreshedCompletedJobs.current.has(j.id));
    unseen.forEach(j => refreshedCompletedJobs.current.add(j.id));
    if (unseen.some(j => !datasets.some(d => d.id === j.result?.dataset_id))) {
      // 백그라운드에서 누락된 Job 결과를 목록에 보강하는 best-effort 조회다.
      // 실패를 업로드·Job 제출 실패로 표현하지 않으며 다음 기회에 재조회한다.
      catalog.refresh().catch(() => {
        unseen.forEach(j => refreshedCompletedJobs.current.delete(j.id));
        setNotice(current => current || "일부 Job 결과 Dataset 목록을 갱신하지 못했습니다. 목록을 다시 조회하세요.");
      });
    }
  }, [dataJobs.jobs, datasets]);

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
      catalog.upsert(item);
      setSelected(item.id);
      chooseDataset(item.id, "preview");
    });
  }

  return <PlatformShell actor={actor} section={tab} modelContext={context}
    pageTitle={tab === "learning" ? learningTypeGuides[context.taxonomyId ?? ""]?.title
      : tab === "task" ? taskGuides[context.taxonomyId ?? ""]?.title
      : resource ? { model: "모델 상세", scoring: "점수 결과", policy: "판정 정책",
        evaluation: "평가 결과", explanation: "설명 결과" }[resource.kind] : undefined}
    modelResource={resource} onNavigate={setTab}
    onOpenTaxonomy={(target, id) => navigateWithContext(target, { taxonomyId: id })}
    onSignOut={onSignOut} canConfigure={canConfigure}
    brandName={brandName} brandReady={brandReady} brandLoadError={brandLoadError}
    onBrandChange={onBrandChange} onBrandRetry={onBrandRetry}
    canInspectStorage={canInspectStorage} canViewAudit={canViewAudit}
    >
      {error && <div role="alert" className="alert error">
        <div className="alert-body">
          <strong>{error.message}</strong>
          {error.guidance && <p>{error.guidance}</p>}
          {error.reference && <small className="alert-reference">{error.reference}</small>}
        </div>
        <button onClick={() => setError(null)} aria-label="오류 닫기">×</button></div>}
      {catalog.readError && ["overview", "understanding", "preview", "profile", "preparation", "topic", "pipelines"].includes(tab) &&
        <div role="alert" className="alert error">
          Dataset 목록을 확인하지 못했습니다. 현재 화면의 데이터 선택이 제한될 수 있습니다.
          <details><summary>문제 정보</summary>{catalog.readError}</details>
          <button type="button" className="secondary" onClick={() => void catalog.retry()}>
            Dataset 다시 조회
          </button>
        </div>}
      {dataJobs.jobsError && tab === "jobs" &&
        <div role="alert" className="alert error">
          작업 이력을 새로 확인하지 못했습니다. 실행이 실패했다는 의미는 아닙니다.
          <details><summary>문제 정보</summary>{dataJobs.jobsError}</details>
          <button type="button" className="secondary" onClick={() => void dataJobs.retryJobs()}>
            작업 이력 다시 조회
          </button>
        </div>}
      {dataJobs.explorationsError && tab === "jobs" &&
        <div role="alert" className="alert error">
          데이터 탐색 작업 이력을 새로 확인하지 못했습니다.
          <details><summary>문제 정보</summary>{dataJobs.explorationsError}</details>
          <button type="button" className="secondary" onClick={() => void dataJobs.retryExplorations()}>
            탐색 이력 다시 조회
          </button>
        </div>}
      {uploads.policyError && tab === "preparation" &&
        <div role="status" className="alert error">
          업로드 정책을 조회할 수 없어 새 파일 등록이 제한될 수 있습니다.
          <details><summary>문제 정보</summary>{uploads.policyError}</details>
        </div>}
      {notice && <div role="status" className="alert success">{notice}
        <button onClick={() => setNotice("")} aria-label="알림 닫기">×</button></div>}
      {uploads.pendingDelimited && <DelimitedImport file={uploads.pendingDelimited} disabled={busy}
        onCancel={uploads.cancelDelimited}
        onConfirm={file => { void uploads.upload(file); }} />}
      {uploads.progress && <UploadProgressStatus progress={uploads.progress} />}
      {tab === "overview" && <PlatformOverview brandName={brandName}
        datasets={datasets.map(d => ({ id: d.id, name: d.name, kind: d.kind }))}
        canViewModels={actor.capabilities.includes("job.read")}
        canWrite={canWrite} canPrepare={canRun}
        onNavigate={setTab} onOpenLearningType={taxonomyLinks.onOpenLearningType}
        onOpenDataset={id => chooseDataset(id, "understanding")} />}
      {context.pipelineRunId && tab !== "pipelines" && <div className="pipeline-return">
        <button className="secondary" onClick={() => navigateWithContext("pipelines", {
          pipelineRunId: context.pipelineRunId,
        })}>파이프라인 실행 결과로 복귀 →</button>
        <span className="hint">원래 실행의 결과는 유지되며 추가 계산은 별도 결과로 남습니다.</span>
      </div>}
      {tab === "pipelines" && (canViewPipelineRuns
        ? <PipelineWorkspace runId={context.pipelineRunId ?? ""} datasetId={context.datasetId ?? ""} datasets={datasets}
          canTrain={canOperatePipelines} canRun={canRun} canCancel={canCancel} canOpenMLflow={canInspectStorage || canViewAudit}
          hasMoreDatasets={Boolean(catalog.nextCursor)} onLoadMoreDatasets={loadMoreDatasets}
          onSelectDataset={datasetId => selectContext({ ...context, datasetId })}
          onSelectRun={(pipelineRunId, datasetId) => navigateWithContext("pipelines", { pipelineRunId, datasetId })}
          onOpenJob={(id, datasetId) => navigateWithContext("jobs", { datasetId, dataJobId: id, dataJobKind: "data", pipelineRunId: context.pipelineRunId })}
          onOpenResource={openResource} onOpenDataset={id => navigateWithContext("understanding", {
            datasetId: id, pipelineRunId: context.pipelineRunId,
          })} />
        : <section className="panel"><h2>파이프라인 조회 권한 없음</h2>
          <p>실행 결과 조회에는 작업 조회 권한이 필요합니다. 준비된 모델은 모델 활용에서 선택해 데이터에 적용할 수 있습니다.</p>
          <button type="button" onClick={() => setTab("ml")}>모델 활용 →</button></section>)}
      {tab === "learning" && <LearningTypePage id={context.taxonomyId ?? ""} links={taxonomyLinks} />}
      {tab === "task" && <AnalysisTaskPage id={context.taxonomyId ?? ""} links={taxonomyLinks} />}
      {tab === "topic" && <ActivityPlayground datasets={datasets} selectedDatasetId={context.datasetId}
        preferredModelId={context.modelId} onSelectModel={modelId => selectContext({ ...context, modelId })}
        isAdministrator={actor.roles.includes("administrator")}
        onSelectDataset={datasetId => selectContext({ ...context, datasetId })}
        hasMoreDatasets={Boolean(catalog.nextCursor)} onLoadMoreDatasets={loadMoreDatasets}
        onDatasetRegistered={catalog.upsert} canRun={canRun} canCancel={canCancel} canUpload={canWrite}
        canInfer={actor.capabilities.includes("model.infer")} onOpenResource={openResource} links={taxonomyLinks} />}
      {isDataArea && <DataSourceBar datasets={datasets} loading={catalog.loading} readError={Boolean(catalog.readError)} selected={selected} canWrite={canWrite} busy={busy}
          uploadRef={uploadRef} uploadPolicy={uploads.policy}
          onFile={file => {
            if (uploads.choose(file) === "confirm") { setError(""); setNotice(""); }
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
          hasMore={Boolean(catalog.nextCursor)} onLoadMore={() => void loadMoreDatasets()}
          pendingLabel={selected && !dataset ? (requested.error ? "선택한 Dataset · 조회 실패" : "선택한 Dataset · 조회 중") : undefined} />}
      {isDataArea && selected && !dataset && <section className="panel" aria-label="선택한 Dataset 조회">
        {requested.error ? <ErrorNoticeView title="선택한 Dataset을 열지 못했습니다" notice={requested.error}>
          <p className="hint">다른 계정의 Dataset이거나 삭제된 Dataset은 열 수 없습니다.</p>
          <button type="button" className="secondary" onClick={() => setReloadRevision(value => value + 1)}>선택 데이터 다시 조회</button>
        </ErrorNoticeView>
          : <p role="status">{requested.loading ? "선택한 Dataset을 불러오는 중입니다." : "선택한 Dataset의 접근 권한을 확인하고 있습니다."}</p>}
      </section>}
      {!selected && isDataArea && <div className="empty">
        <h2>{tab === "preparation" ? "데이터 준비 시작" : "첫 데이터 탐색"}</h2>
        <p>{!canWrite
          ? "조회 전용 계정입니다. 이 계정 소유의 Dataset이 있으면 위의 Dataset 선택에서 골라 살펴볼 수 있습니다."
          : tab === "preparation"
          ? "데이터 준비는 탐색 분석 없이도 시작할 수 있습니다. 파일을 가져오거나 기존 Dataset을 선택하세요."
          : "CSV·Parquet·탭 구분 TSV/TXT, Iceberg 테이블 또는 합성 예제로 시작하세요."}</p>
        {canWrite && <button className="secondary" onClick={() => uploadRef.current?.click()}>
          파일 선택 (CSV·Parquet·TSV·TXT)
        </button>}
        {/* 예제는 Dataset으로 등록되므로 등록 권한이 있는 계정에만 보인다. */}
        {canWrite && <AvailableExamples busy={busy}
          startLabel={tab === "preparation"
            ? "이 예제로 데이터 준비 시작 →" : undefined}
          onUse={id => void selectExample(id)} />}
      </div>}
      {dataset && isDataArea && <>
        <div className="dataset-hero">
          <div><h2>{dataset.name}</h2>
            <p>{dataset.kind === "iceberg" ? `Iceberg 테이블 · Snapshot ${dataset.version}` : "파일"}</p></div>
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
              void dataJobs.refreshJobs().catch(() => setNotice(current => current ||
                "분석 Job은 접수됐습니다. 전체 Jobs에서 목록을 다시 조회할 수 있습니다."
              ));
            }}
            onViewJobs={jobId => openDataJob(jobId)}
            overview={overview} overviewStatus={overviewStatus}
            overviewError={overviewError} onRetryOverview={retryOverview} />
        </section>}
        {tab === "preview" && <DatasetPreviewPanel datasetId={selected} revision={reloadRevision}
          preview={preview} previewStatus={previewStatus} previewError={previewError} overview={overview}
          columnNames={columnNames} busy={busy} query={query} onRetry={() => setReloadRevision(v => v + 1)} />}
        {tab === "profile" && <section className="panel" role="tabpanel"
          id="sos-panel-profile" aria-labelledby="sos-tab-profile" tabIndex={0}>
          <DiscoveryPanel key={selected + ":" + reloadRevision} datasetId={selected}
            canCreate={canRun} draft={activeExplorationDraft} onDraftChange={changeExploration}
            overview={overview} overviewStatus={overviewStatus}
            overviewError={overviewError} onRetryOverview={retryOverview}
            onCreated={dataJobs.refreshExplorations} onOpenJob={id => openDataJob(id, "exploration")} />
          <ProfileStatistics profile={profile} profileStatus={profileStatus} profileError={profileError}
            preview={preview} columnNames={columnNames} selection={profileSelection} canRun={canRun} busy={busy}
            onRunFullProfile={() => void submit("profile")} onRetry={() => setReloadRevision(v => v + 1)} />
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
      {tab === "ml" && !resource && <ModelCatalog canRun={canRun}
        datasetId={context.datasetId} datasetName={datasets.find(item => item.id === context.datasetId)?.name}
        onClearDataset={() => selectContext({ ...context, datasetId: undefined })}
        onOpenModel={id => openResource("model", id)} onScoreModel={model => void applyModel(model)} />}
      {resource && <MLResourceDetail key={resource.kind + ":" + resource.id}
        kind={resource.kind} id={resource.id} onOpenResource={openResource}
        canRun={canRun} canCancel={canCancel}
        canManageModel={actor.capabilities.includes("model.manage")} isAdministrator={actor.roles.includes("administrator")}
        onOpenServing={() => navigateWithContext("serving", { pipelineRunId: context.pipelineRunId })}
        onOpenModelCatalog={() => navigateWithContext("ml", { datasetId: context.datasetId })}
        onScoring={startScoring} onApplyModel={model => void applyModel(model)} />}
      {tab === "scoring" && !resource && <ScoringWorkspace
        datasets={datasets} preferredModelId={context.modelId ?? ""}
        selectedDatasetId={context.datasetId ?? ""}
        onSelectDataset={datasetId => selectContext({ ...context, datasetId })}
        hasMoreDatasets={Boolean(catalog.nextCursor)} onLoadMoreDatasets={loadMoreDatasets}
        canUpload={canWrite} onDatasetRegistered={catalog.upsert}
        onModelSelected={modelId => selectContext({ ...context, modelId })} onNavigate={setTab}
        isAdministrator={actor.roles.includes("administrator")} canRun={canRun} canCancel={canCancel} onOpenResource={openResource} />}
      {(["policies","evaluations","explanations"] as Tab[]).includes(tab) && !resource &&
        <DecisionWorkspace
          key={tab} view={tab === "policies" ? "policy" : tab === "evaluations" ? "evaluation" : "explanation"}
          preferredScoringId={context.scoringId ?? ""}
          onScoringSelected={scoringId => selectContext({ ...context, scoringId })}
          onNavigate={setTab} canRun={canRun} canCancel={canCancel}
          onOpenResource={openResource} datasets={datasets} />}
      {actor.capabilities.includes("model.manage") && tab === "serving" && <ServingOperations onOpenResource={openResource}
        canOpenOperatorTools={canInspectStorage || canViewAudit} />}
      {canInspectStorage && tab === "storage" && <StoragePanel />}
      {canViewAudit && tab === "audit" && <AuditPanel />}
      {(tab === "jobs" || tab === "ml-jobs") && <ActivityWorkspace
        active={tab === "ml-jobs" ? "ml" : "data"}
        onChange={view => setTab(view === "ml" ? "ml-jobs" : "jobs")}
        onNavigate={setTab} onOpenResource={openResource} datasets={datasets}
        canCancel={canCancel}>
        {tab === "jobs" && <JobsPanel jobs={[...dataJobs.jobs, ...dataJobs.explorations].sort((a, b) =>
          b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))}
          datasets={datasets} busy={busy} canCancel={canCancel}
          selectedJobId={context.dataJobId} selectedJobKind={context.dataJobKind}
          nextCursor={dataJobs.jobsCursor} discoveryNextCursor={dataJobs.explorationsCursor}
          onLoadMore={() => void loadMoreJobs()}
          onLoadMoreDiscovery={() => void loadMoreDiscoveryJobs()}
          onRefresh={() => void dataJobs.refreshAll()} loading={dataJobs.loading}
          readError={Boolean(dataJobs.jobsError || dataJobs.explorationsError)}
          onSelectJob={openDataJob}
          onOpen={id => void openJobDataset(id)} />}
      </ActivityWorkspace>}
  </PlatformShell>;
}
