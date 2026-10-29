import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob, uploadFile } from "./api";
import { checkModelDatasetInput, ModelDatasetCompatibility } from "./ModelDatasetCompatibility";
import { usePagedJobs } from "./usePagedJobs";
import { ScoringObservationExplorer } from "./ScoringObservationExplorer";
import type { MLResourceKind } from "./PlatformNavigation";
import type {
  Dataset, DatasetColumns, MLScoringJob, MLScoringJobSummary, ModelRevision, ScoringResult,
} from "./types";
import { formatDtype, jobStatusLabel } from "./display-format";

type Props = {
  selectedDatasetId?: string;
  onSelectDataset?: (id: string) => void;
  model: ModelRevision;
  datasets: Dataset[];
  hasMoreDatasets?: boolean;
  onLoadMoreDatasets?: () => Promise<void>;
  canRun: boolean;
  canCancel: boolean;
  onOpenResult: (id: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  canUpload?: boolean;
  onDatasetRegistered?: (dataset: Dataset) => void;
  onResultViewChange?: (visible: boolean) => void;
  onTryObservation?: (
    features: Record<string, number | null>, rowIndex: number, score: number,
    scoringResultId: string, modelRevisionId: string,
  ) => string | null;
};

const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

/** 학습된 모델을 다른 Dataset에 적용하는 독립 Scoring 경험. */
export function MLScoringPanel({
  selectedDatasetId, onSelectDataset, model, datasets, hasMoreDatasets = false, onLoadMoreDatasets, canRun, canCancel, onOpenResult, onOpenResource,
  canUpload = false, onDatasetRegistered, onResultViewChange, onTryObservation,
}: Props) {
  const [localDatasetId, setLocalDatasetId] = useState("");
  const datasetId = selectedDatasetId ?? localDatasetId;
  function setDatasetId(id: string) {
    setLocalDatasetId(id);
    onSelectDataset?.(id);
  }
  const [loadingMoreDatasets, setLoadingMoreDatasets] = useState(false);
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [labelColumn, setLabelColumn] = useState("");
  // null은 검증 가능한 시간 컬럼 'ts' 기본 제안, 빈 문자열은 사용자가 보존하지 않음을 뜻한다.
  const [timeColumnChoice, setTimeColumnChoice] = useState<string | null>(null);
  const [columnStatus, setColumnStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [columnsDatasetId, setColumnsDatasetId] = useState("");
  const [columnError, setColumnError] = useState("");
  const [columnsRetry, setColumnsRetry] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadProgress, setUploadProgress] = useState<{ loaded: number; total: number; phase: "transferring" | "registering" } | null>(null);
  const [selectedJob, setSelectedJob] = useState<MLScoringJob | null>(null);
  const [previousJobNotice, setPreviousJobNotice] = useState(false);
  const [result, setResult] = useState<ScoringResult | null>(null);
  const [resultError, setResultError] = useState("");
  const [resultRetry, setResultRetry] = useState(0);
  const resultRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [loadingHistoryId, setLoadingHistoryId] = useState<string | null>(null);
  const historySelectionRequest = useRef(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const { items: history, nextCursor, refresh, loadMore } =
    usePagedJobs<MLScoringJobSummary>("/api/ml/scoring/jobs");

  useEffect(() => {
    let alive = true;
    setColumns([]);
    setColumnsDatasetId("");
    setLabelColumn("");
    setTimeColumnChoice(null);
    setColumnError("");
    setColumnStatus(datasetId ? "loading" : "idle");
    if (datasetId) {
      void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(datasetId)}/columns`)
        .then(data => {
          if (alive) {
            setColumns(data.columns); setColumnsDatasetId(datasetId); setColumnStatus("ready");
          }
        })
        .catch(e => {
          if (alive) { setColumnError(formatError(e)); setColumnStatus("error"); }
        });
    }
    return () => { alive = false; };
  }, [datasetId, columnsRetry]);

  useEffect(() => {
    if (!selectedJob || TERMINAL.has(selectedJob.status)) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const next = await request<MLScoringJob>(
          `/api/ml/scoring/jobs/${encodeURIComponent(selectedJob.id)}`,
        );
        if (alive) {
          setSelectedJob(next);
          setError("");
          if (TERMINAL.has(next.status)) {
            if (historyOpen) void refresh().catch(e => { if (alive) setHistoryError(formatError(e)); });
          }
        }
      } catch (e) {
        if (alive) setError(formatError(e));
      } finally {
        if (alive) timer = setTimeout(() => void poll(), 2500);
      }
    };
    timer = setTimeout(() => void poll(), 1500);
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [selectedJob?.id, selectedJob?.status, historyOpen]);

  useEffect(() => {
    let alive = true;
    setResult(null);
    setResultError("");
    const resultId = selectedJob?.result?.scoring_result_id;
    if (selectedJob?.status === "Succeeded" && typeof resultId === "string") {
      void request<ScoringResult>(`/api/ml/scoring/results/${encodeURIComponent(resultId)}`)
        .then(data => { if (alive) setResult(data); })
        .catch(e => { if (alive) setResultError(formatError(e)); });
    }
    return () => { alive = false; };
  }, [selectedJob?.status, selectedJob?.result?.scoring_result_id, resultRetry]);

  useEffect(() => {
    if (selectedJob?.id) resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selectedJob?.id]);

  const checkedColumns = columnStatus === "ready" && columnsDatasetId === datasetId;
  const inputCheck = checkedColumns ? checkModelDatasetInput(model, columns) : null;
  const timeColumns = checkedColumns
    ? columns.filter(col => !model.features.includes(col.name) &&
        col.name !== labelColumn && /^(String|Utf8|Datetime)/i.test(col.dtype)) : [];
  const timeColumn = timeColumnChoice === null
    ? timeColumns.find(col => col.name === "ts")?.name ?? ""
    : timeColumns.some(col => col.name === timeColumnChoice) ? timeColumnChoice : "";
  const inputReady = datasetId !== "" && inputCheck?.ready === true;
  function chooseDataset(id: string) {
    historySelectionRequest.current += 1;
    setLoadingHistoryId(null);
    if (selectedJob && !TERMINAL.has(selectedJob.status)) setPreviousJobNotice(true);
    setDatasetId(id);
    setColumnStatus("idle");
    setColumnsDatasetId("");
    setColumns([]);
    setLabelColumn("");
    setTimeColumnChoice(null);
    // 완료된 결과를 조사하는 동안 다른 Dataset을 준비하더라도 기존 근거는 유지한다.
    // 새 Job을 명시적으로 제출하거나 진행 중인 Job에서 전환할 때만 기존 선택을 해제한다.
    if (!visibleResult) {
      setSelectedJob(null);
      setResult(null);
    }
    setError("");
  }
  /** 파일 등록과 Scoring 제출은 서로 다른 완료 사건이다. */
  async function addDataset(file: File) {
    if (uploading || submitted || !canUpload || !onDatasetRegistered) return;
    setUploading(true); setUploadError("");
    setUploadProgress({ loaded: 0, total: file.size, phase: "transferring" });
    try {
      const data = await uploadFile<Dataset>("/api/datasets/files", file,
        (loaded, total) => setUploadProgress({ loaded, total, phase: "transferring" }),
        () => setUploadProgress(previous => previous ? { ...previous, phase: "registering" } : null));
      // 201 등록 성공 후 화면 목록을 업데이트하며, 점수 계산은 사용자가 명시적으로 시작한다.
      onDatasetRegistered(data);
      chooseDataset(data.id);
    } catch (cause) { setUploadError(formatError(cause)); }
    finally { setUploading(false); setUploadProgress(null); }
  }

  async function create() {
    if (submitted || uploading || !canRun || !inputReady) return;
    historySelectionRequest.current += 1;
    setLoadingHistoryId(null);
    setSubmitted(true);
    setError("");
    try {
      const job = await submitJob<MLScoringJob>("/api/ml/scoring/jobs", {
        model_revision_id: model.id,
        dataset_id: datasetId,
        label_column: labelColumn || null,
        observation_time_column: timeColumn || null,
      });
      // 선택한 Job/Result가 같으면 기존 결과를 유지한다. 새로운 판본은 위의 일치 검사로 격리한다.
      setSelectedJob(job);
      if (historyOpen) void refresh().catch(e => setHistoryError(formatError(e)));
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSubmitted(false);
    }
  }

  async function selectHistory(id: string) {
    if (submitted || uploading) return;
    const requestNumber = ++historySelectionRequest.current;
    setLoadingHistoryId(id);
    setHistoryError("");
    try {
      const restored = await request<MLScoringJob>(
        `/api/ml/scoring/jobs/${encodeURIComponent(id)}`,
      );
      if (requestNumber !== historySelectionRequest.current) return;
      if (restored.id !== id || restored.spec.model_revision_id !== model.id)
        throw new Error("현재 모델 또는 선택한 실행 기록과 조회 결과가 다릅니다.");
      setSelectedJob(restored);
      setError("");
    } catch (e) {
      if (requestNumber === historySelectionRequest.current) setHistoryError(formatError(e));
    } finally {
      if (requestNumber === historySelectionRequest.current) setLoadingHistoryId(null);
    }
  }

  async function cancel() {
    if (!selectedJob || !canCancel) return;
    try {
      const updated = await request<MLScoringJob>(
        `/api/ml/scoring/jobs/${encodeURIComponent(selectedJob.id)}/cancel`,
        { method: "POST" },
      );
      setSelectedJob(current => current?.id === updated.id ? updated : current);
    } catch (e) {
      setError(formatError(e));
    }
  }

  const hasResultReference = typeof selectedJob?.result?.scoring_result_id === "string" &&
    selectedJob.result.scoring_result_id.length > 0;
  const visibleResult = result && selectedJob?.status === "Succeeded" &&
    result.id === selectedJob.result?.scoring_result_id &&
    result.model_revision_id === model.id ? result : null;

  useEffect(() => {
    onResultViewChange?.(Boolean(visibleResult));
  }, [onResultViewChange, visibleResult !== null]);

  const available = history.filter(job =>
    job.spec.model_revision_id === model.id);
  const value = (n: unknown) => typeof n === "number" && Number.isFinite(n)
    ? n.toLocaleString("ko-KR", { maximumFractionDigits: 5 }) : "미정의";

  return <section className={`panel ml-scoring-panel${visibleResult ? " scoring-has-result" : ""}`} aria-label="독립 이상 점수 계산">
    {previousJobNotice && <div role="status" className="notice-blocked">
      이전 Dataset에서 시작한 점수 계산 Job은 자동 취소되지 않습니다.
      완료 여부는 작업 이력에서 확인할 수 있습니다.
      <button type="button" className="secondary" onClick={() => setPreviousJobNotice(false)}>
        안내 닫기
      </button>
    </div>}
    {error && <div role="alert" className="notice-blocked">
      <strong>모델 점수 계산 또는 상태 확인에 문제가 생겼습니다.</strong>
      <details><summary>요청 오류와 복구 정보</summary><p>{error}</p></details>
    </div>}
    {selectedJob && <div className="result scoring-result-first" ref={resultRef}>
      {!visibleResult && <h4>Dataset 분석 · {jobStatusLabel(selectedJob.status)}</h4>}
      {!visibleResult && <p className="hint">분석 중인 Dataset: <strong>{
        datasets.find(d => d.id === selectedJob.dataset_id)?.name ?? "현재 목록 밖의 Dataset"}</strong></p>}
      {!TERMINAL.has(selectedJob.status) && <p className="scoring-result-preview">
        완료되면 이 자리에 전체 관측값의 점수 분포와 점수가 높은 관측값이 표시됩니다.</p>}
      {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
        !selectedJob.cancel_requested &&
        <button type="button" className="secondary scoring-cancel" onClick={() => void cancel()}>
          점수 계산 취소 요청</button>}
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소가 요청됐습니다. 종료가 확인될 때까지 기다립니다.</p>}
      {selectedJob.status === "Failed" && <p role="alert">점수 계산 실패 · {selectedJob.error ?? "실행 오류"}
        {selectedJob.error_code && <small> ({selectedJob.error_code})</small>}</p>}
      {selectedJob.status === "Succeeded" &&
        !hasResultReference &&
        <p role="alert">점수 계산은 완료됐지만 결과 참조가 없습니다. 작업 이력에서 실행 근거를 확인해 주세요.</p>}
      {selectedJob.status === "Succeeded" && hasResultReference &&
        resultError && <div role="alert" className="notice-blocked">
          <p>점수 계산은 완료됐지만 결과를 불러오지 못했습니다: {resultError}</p>
          <button type="button" className="secondary"
            onClick={() => setResultRetry(current => current + 1)}>결과만 다시 조회</button>
        </div>}
      {selectedJob.status === "Succeeded" && hasResultReference &&
        !visibleResult && !resultError &&
        <p role="status">완료된 분석의 결과 판본을 조회하는 중입니다.</p>}
      {visibleResult && <div className="scoring-result-complete">
        {visibleResult.dataset_id === model.dataset_id && visibleResult.dataset_version === model.dataset_version &&
          <p className="hint">학습에 사용한 동일 Dataset 판본의 결과입니다.
            독립된 Test 데이터에서 얻은 성능으로 해석하지 마세요.</p>}
        <ScoringObservationExplorer key={visibleResult.id} result={visibleResult} canRun={canRun}
          canCancel={canCancel} onOpenResource={onOpenResource} onTryObservation={onTryObservation} />
        {(typeof visibleResult.ranking_metrics?.average_precision === "number" ||
          typeof visibleResult.ranking_metrics?.roc_auc === "number") && <details>
          <summary>정답 레이블 기반 순위 지표</summary>
          {typeof visibleResult.ranking_metrics?.average_precision === "number" &&
            <p>Average Precision · {value(visibleResult.ranking_metrics.average_precision)}</p>}
          {typeof visibleResult.ranking_metrics?.roc_auc === "number" &&
            <p>ROC-AUC · {value(visibleResult.ranking_metrics.roc_auc)}</p>}
          <p className="hint">정답 레이블과 계산 조건이 충족된 경우에만 표시합니다.
            판정 정책과 판정 정확도는 별도 평가 대상입니다.</p>
        </details>}
        <button type="button" className="secondary" onClick={() => onOpenResult(visibleResult.id)}>
          모델·Dataset 판본과 기술 근거 열기 →
        </button>
      </div>}
    </div>}
    {(() => {
      const form = <div className="scoring-next-analysis-body">
    <div className="form-row">
      <label>기존 Dataset 선택
        <select aria-label="모델에 적용할 Dataset" value={datasetId}
          disabled={submitted || uploading}
          onChange={e => chooseDataset(e.target.value)}>
          <option value="">분석할 Dataset 선택</option>
          {datasetId && !datasets.some(item => item.id === datasetId) && <option value={datasetId}>선택한 Dataset · 목록 외</option>}
          {datasets.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label>정답 레이블 (선택 · 0/1 숫자형)
        <select aria-label="Scoring 정답 레이블" value={labelColumn}
          disabled={submitted || uploading}
          onChange={e => setLabelColumn(e.target.value)}>
          <option value="">레이블 없음</option>
          {/* 서버는 결측 없는 0/1 숫자형 레이블만 받는다. 문자열·시간 컬럼은 후보가 아니다. */}
          {columns.filter(col => !model.features.includes(col.name) &&
            /^(?:U?Int(?:8|16|32|64)|Float(?:32|64))$/.test(col.dtype)).map(col =>
            <option key={col.name} value={col.name}>{col.name} ({formatDtype(col.dtype)})</option>)}
        </select>
      </label>
      {checkedColumns && <label>관측 시각 (선택)
        <select aria-label="Scoring 관측 시각 컬럼" value={timeColumn}
          disabled={submitted || uploading}
          onChange={e => setTimeColumnChoice(e.target.value)}>
          <option value="">관측 시각 보존 안 함</option>
          {timeColumns.map(col => <option key={col.name} value={col.name}>
            {col.name} ({formatDtype(col.dtype)})
          </option>)}
        </select>
      </label>}
    </div>
    {datasets.length === 0 && canUpload && onDatasetRegistered && <details className="scoring-file-input"
      open={datasets.length === 0 || uploading || Boolean(uploadError) || undefined}>
      <summary>새 파일 등록·분석 (CSV·Parquet)</summary>
      <label>등록할 파일
        <input type="file" accept=".csv,.parquet" disabled={uploading || submitted}
          onChange={event => {
            const file = event.currentTarget.files?.[0];
            if (file) void addDataset(file);
            event.currentTarget.value = "";
          }} />
      </label>
      <p className="hint">CSV·Parquet 파일을 Dataset으로 등록한 뒤 입력 조건을 확인합니다.
        등록이 끝나도 점수 계산은 자동 제출하지 않습니다. 선택 모델에 기록된 Feature 이름·자료형의 데이터가 필요합니다.</p>
      {uploading && <div role="status" className="scoring-upload-status">
        <p>{uploadProgress?.phase === "registering"
          ? "파일 전송 완료 · 서버에서 검증하고 Dataset을 등록하고 있습니다."
          : `파일 전송 중 · ${uploadProgress?.total ? Math.floor(100 * uploadProgress.loaded / uploadProgress.total) : 0}%`}</p>
        {uploadProgress?.phase === "transferring" && uploadProgress.total > 0 &&
          <progress max={uploadProgress.total} value={uploadProgress.loaded} aria-label="파일 전송 진행률" />}
      </div>}
      {uploadError && <div role="alert" className="notice-blocked">
        <strong>파일을 등록하지 못했습니다.</strong>
        <p>파일 형식과 크기, 연결 상태를 확인해 주세요. 등록 여부가 불확실하면 Dataset 목록에서 먼저 확인해 주세요.</p>
        <details><summary>오류 진단 정보</summary><p>{uploadError}</p></details>
      </div>}
    </details>}
    {hasMoreDatasets && onLoadMoreDatasets && <button type="button" className="secondary"
      disabled={loadingMoreDatasets} onClick={() => {
        setLoadingMoreDatasets(true);
        void onLoadMoreDatasets().catch(e => setError(formatError(e)))
          .finally(() => setLoadingMoreDatasets(false));
      }}>
      {loadingMoreDatasets ? "Dataset 불러오는 중…" : "Dataset 더 보기"}
    </button>}
    {columnStatus === "loading" && <p role="status">Dataset의 입력 컬럼을 확인하는 중입니다.</p>}
    {columnStatus === "error" && <div role="alert"><p>입력 컬럼을 조회하지 못했습니다: {columnError}</p>
      <button type="button" className="secondary" onClick={() => setColumnsRetry(value => value + 1)}>입력 구조 재조회</button></div>}
    {checkedColumns && <ModelDatasetCompatibility model={model} columns={columns} />}
    {datasetId === model.dataset_id && <p className="hint">
      모델 학습에 사용한 Dataset과 같은 ID입니다. 판본이 같다면 해당 점수는 독립 Test 성능이 아닙니다.
    </p>}
    {!datasetId && <p className="scoring-action-hint">분석할 Dataset을 먼저 선택하세요.</p>}
    {datasetId && !inputReady && columnStatus === "loading" && <p className="scoring-action-hint">Dataset의 입력 조건을 확인하는 중입니다.</p>}
    {!canRun && <p className="scoring-action-hint">현재 계정에는 점수 계산 실행 권한이 없습니다.</p>}
    <button disabled={!canRun || submitted || uploading || !inputReady} onClick={() => void create()}>
      {submitted ? "점수 계산 요청 중…" : "이 Dataset 분석 시작 →"}
    </button>
    {datasets.length > 0 && canUpload && onDatasetRegistered && <details className="scoring-file-input"
      open={datasets.length === 0 || uploading || Boolean(uploadError) || undefined}>
      <summary>새 파일 등록·분석 (CSV·Parquet)</summary>
      <label>등록할 파일
        <input type="file" accept=".csv,.parquet" disabled={uploading || submitted}
          onChange={event => {
            const file = event.currentTarget.files?.[0];
            if (file) void addDataset(file);
            event.currentTarget.value = "";
          }} />
      </label>
      <p className="hint">CSV·Parquet 파일을 Dataset으로 등록한 뒤 입력 조건을 확인합니다.
        등록이 끝나도 점수 계산은 자동 제출하지 않습니다. 선택 모델에 기록된 Feature 이름·자료형의 데이터가 필요합니다.</p>
      {uploading && <div role="status" className="scoring-upload-status">
        <p>{uploadProgress?.phase === "registering"
          ? "파일 전송 완료 · 서버에서 검증하고 Dataset을 등록하고 있습니다."
          : `파일 전송 중 · ${uploadProgress?.total ? Math.floor(100 * uploadProgress.loaded / uploadProgress.total) : 0}%`}</p>
        {uploadProgress?.phase === "transferring" && uploadProgress.total > 0 &&
          <progress max={uploadProgress.total} value={uploadProgress.loaded} aria-label="파일 전송 진행률" />}
      </div>}
      {uploadError && <div role="alert" className="notice-blocked">
        <strong>파일을 등록하지 못했습니다.</strong>
        <p>파일 형식과 크기, 연결 상태를 확인해 주세요. 등록 여부가 불확실하면 Dataset 목록에서 먼저 확인해 주세요.</p>
        <details><summary>오류 진단 정보</summary><p>{uploadError}</p></details>
      </div>}
    </details>}
    <details className="scoring-input-guidance">
      <summary>입력 조건과 결과 해석 안내</summary>
      <p className="hint">모델 학습 때의 Feature 이름·자료형이 일치해야 합니다. 실제 값은 실행 시 검증합니다.
        결과 레이블을 지정한 경우에만 지원되는 순위 지표를 계산합니다.
        원본의 명시적 시간대가 있는 관측 시각만 결과에 보존하며,
        관측 시각의 보존만으로 시간 분할이나 독립 Test 평가를 의미하지 않습니다.
        이상 점수는 사건 확률이 아닙니다.</p>
    </details>
    <details className="scoring-history" onToggle={event => {
      setHistoryOpen(event.currentTarget.open);
      if (event.currentTarget.open) {
        setHistoryError("");
        void refresh().catch(cause => setHistoryError(formatError(cause)));
      }
    }}><summary>이 모델의 이전 점수 계산</summary>
    <div className="steps">
      {historyError && <p role="alert">이전 점수 계산 이력을 확인하지 못했습니다: {historyError}</p>}
      <strong>이 모델의 점수 계산 이력</strong>
      {available.map(item => <button key={item.id} className="secondary"
        type="button" disabled={submitted || uploading}
        aria-pressed={selectedJob?.id === item.id}
        onClick={() => void selectHistory(item.id)}>
        {item.id.slice(0, 8)} · {jobStatusLabel(item.status)}
        {loadingHistoryId === item.id ? " · 결과 조회 중…" : selectedJob?.id === item.id ? " · 선택됨" : " · 결과 확인"}
      </button>)}
      {available.length === 0 && <p className="hint">현재 조회한 이력에 이 모델의 점수 계산 기록이 없습니다.</p>}
      {nextCursor && <button className="secondary"
        onClick={() => void loadMore().catch(e => setHistoryError(formatError(e)))}>
        이전 점수 계산 더 보기
      </button>}
    </div>
    </details>
      </div>;
      return selectedJob
        ? <details key={visibleResult ? "completed" : "selected"} className="scoring-next-analysis">
            <summary>다른 Dataset 분석</summary>{form}</details>
        : form;
    })()}
  </section>;
}
