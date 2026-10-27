import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob, uploadFile } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import { ScoringObservationExplorer } from "./ScoringObservationExplorer";
import type { MLResourceKind } from "./PlatformNavigation";
import type {
  Dataset, DatasetColumns, MLScoringJob, MLScoringJobSummary, ModelRevision, ScoringResult,
} from "./types";
import { jobStatusLabel } from "./display-format";

type Props = {
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
};

const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

/** 학습된 모델을 다른 Dataset에 적용하는 독립 Scoring 경험. */
export function MLScoringPanel({
  model, datasets, hasMoreDatasets = false, onLoadMoreDatasets, canRun, canCancel, onOpenResult, onOpenResource,
  canUpload = false, onDatasetRegistered,
}: Props) {
  const [datasetId, setDatasetId] = useState("");
  const [loadingMoreDatasets, setLoadingMoreDatasets] = useState(false);
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [labelColumn, setLabelColumn] = useState("");
  const [columnStatus, setColumnStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [columnsDatasetId, setColumnsDatasetId] = useState("");
  const [columnError, setColumnError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadProgress, setUploadProgress] = useState<{ loaded: number; total: number; phase: "transferring" | "registering" } | null>(null);
  const [selectedJob, setSelectedJob] = useState<MLScoringJob | null>(null);
  const [previousJobNotice, setPreviousJobNotice] = useState(false);
  const [result, setResult] = useState<ScoringResult | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const { items: history, nextCursor, refresh, loadMore } =
    usePagedJobs<MLScoringJobSummary>("/api/ml/scoring/jobs");

  useEffect(() => {
    let alive = true;
    setColumns([]);
    setColumnsDatasetId("");
    setLabelColumn("");
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
  }, [datasetId]);

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
    const resultId = selectedJob?.result?.scoring_result_id;
    if (selectedJob?.status === "Succeeded" && typeof resultId === "string") {
      void request<ScoringResult>(`/api/ml/scoring/results/${encodeURIComponent(resultId)}`)
        .then(data => { if (alive) setResult(data); })
        .catch(e => { if (alive) setError(formatError(e)); });
    }
    return () => { alive = false; };
  }, [selectedJob?.status, selectedJob?.result?.scoring_result_id]);

  useEffect(() => {
    if (selectedJob?.id) resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selectedJob?.id]);

  const checkedColumns = columnStatus === "ready" && columnsDatasetId === datasetId;
  const missingFeatures = checkedColumns
    ? model.features.filter(feature => !columns.some(col => col.name === feature)) : [];
  const inputReady = datasetId !== "" && checkedColumns && missingFeatures.length === 0;
  function chooseDataset(id: string) {
    if (selectedJob && !TERMINAL.has(selectedJob.status)) setPreviousJobNotice(true);
    setDatasetId(id);
    setColumnStatus("idle");
    setColumnsDatasetId("");
    setColumns([]);
    setLabelColumn("");
    setSelectedJob(null);
    setResult(null);
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
    setSubmitted(true);
    setError("");
    try {
      const job = await submitJob<MLScoringJob>("/api/ml/scoring/jobs", {
        model_revision_id: model.id,
        dataset_id: datasetId,
        label_column: labelColumn || null,
      });
      setSelectedJob(job);
      setResult(null);
      if (historyOpen) void refresh().catch(e => setHistoryError(formatError(e)));
    } catch (e) {
      setError(formatError(e));
    } finally {
      setSubmitted(false);
    }
  }

  async function selectHistory(id: string) {
    setError("");
    try {
      setSelectedJob(await request<MLScoringJob>(
        `/api/ml/scoring/jobs/${encodeURIComponent(id)}`,
      ));
    } catch (e) {
      setError(formatError(e));
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

  const available = history.filter(job =>
    job.spec.model_revision_id === model.id);
  const value = (n: unknown) => typeof n === "number" && Number.isFinite(n)
    ? n.toLocaleString("ko-KR", { maximumFractionDigits: 5 }) : "미정의";

  return <section className="panel" aria-label="독립 이상 점수 계산">
    <div className="panel-head"><div>
      <h4>어떤 데이터에 모델을 적용할까요?</h4>
      <p>선택한 Dataset에 학습 모델을 적용해 불변 Scoring Result를 생성합니다.</p>
    </div></div>
    {previousJobNotice && <div role="status" className="pipeline-readiness-blocked">
      이전 Dataset에서 시작한 점수 계산 Job은 자동 취소되지 않습니다.
      완료 여부는 작업 이력에서 확인할 수 있습니다.
      <button type="button" className="secondary" onClick={() => setPreviousJobNotice(false)}>
        안내 닫기
      </button>
    </div>}
    {error && <div role="alert" className="pipeline-readiness-blocked">
      <strong>모델 점수 계산 또는 상태 확인에 문제가 생겼습니다.</strong>
      <details><summary>요청 오류와 복구 정보</summary><p>{error}</p></details>
    </div>}
    {selectedJob && <div className="result scoring-result-first" ref={resultRef}>
      <h4>선택한 Dataset 분석 · {({
        Queued: "대기 중", Running: "진행 중", Succeeded: "완료",
        Failed: "실패", Cancelled: "취소됨",
      } as Record<string, string>)[selectedJob.status] ?? selectedJob.status}</h4>
      <p className="hint">실행에 사용한 Dataset: {datasets.find(d => d.id === selectedJob.spec.dataset_id)?.name ??
        "현재 목록 밖의 Dataset"} · 해당 Job의 입력과 결과를 표시합니다.</p>
      {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
        !selectedJob.cancel_requested &&
        <button className="secondary" onClick={() => void cancel()}>Scoring 취소 요청</button>}
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소가 요청됐습니다. 종료가 확인될 때까지 기다립니다.</p>}
      {selectedJob.status === "Failed" && <p role="alert">Scoring 실패 · {selectedJob.error ?? "실행 오류"}
        {selectedJob.error_code && <small> ({selectedJob.error_code})</small>}</p>}
      {result && <div className="scoring-result-complete">
        {result.dataset_id === model.dataset_id && result.dataset_version === model.dataset_version &&
          <p className="hint">학습에 사용한 동일 Dataset 판본의 결과입니다.
            독립된 Test 데이터에서 얻은 성능으로 해석하지 마세요.</p>}
        <ScoringObservationExplorer key={result.id} result={result} canRun={canRun}
          canCancel={canCancel} onOpenResource={onOpenResource} />
        {(typeof result.ranking_metrics?.average_precision === "number" ||
          typeof result.ranking_metrics?.roc_auc === "number") && <details>
          <summary>정답 레이블 기반 순위 지표</summary>
          {typeof result.ranking_metrics?.average_precision === "number" &&
            <p>Average Precision · {value(result.ranking_metrics.average_precision)}</p>}
          {typeof result.ranking_metrics?.roc_auc === "number" &&
            <p>ROC-AUC · {value(result.ranking_metrics.roc_auc)}</p>}
          <p className="hint">정답 레이블과 계산 조건이 충족된 경우에만 표시합니다.
            판정 정책과 판정 정확도는 별도 평가 대상입니다.</p>
        </details>}
        <button type="button" className="secondary" onClick={() => onOpenResult(result.id)}>
          모델·Dataset 판본과 기술 근거 열기 →
        </button>
      </div>}
    </div>}
    {result && <h4 className="scoring-next-input-heading">다른 Dataset 분석하기</h4>}
    {canUpload && onDatasetRegistered && <div className="scoring-file-input">
      <label>새 파일로 분석 준비
        <input type="file" accept=".csv,.parquet" disabled={uploading || submitted}
          onChange={event => {
            const file = event.currentTarget.files?.[0];
            if (file) void addDataset(file);
            event.currentTarget.value = "";
          }} />
      </label>
      <p className="hint">CSV·Parquet 파일을 Dataset으로 등록한 뒤 입력 조건을 확인합니다.
        등록이 끝나도 점수 계산은 자동 제출하지 않습니다. 원시 로그의 활동 Feature 자동 변환은 아직 지원하지 않습니다.</p>
      {uploading && <div role="status" className="scoring-upload-status">
        <p>{uploadProgress?.phase === "registering"
          ? "파일 전송 완료 · 서버에서 검증하고 Dataset을 등록하고 있습니다."
          : `파일 전송 중 · ${uploadProgress?.total ? Math.floor(100 * uploadProgress.loaded / uploadProgress.total) : 0}%`}</p>
        {uploadProgress?.phase === "transferring" && uploadProgress.total > 0 &&
          <progress max={uploadProgress.total} value={uploadProgress.loaded} aria-label="파일 전송 진행률" />}
      </div>}
      {uploadError && <div role="alert" className="pipeline-readiness-blocked">
        <strong>파일을 등록하지 못했습니다.</strong>
        <p>파일 형식과 크기, 연결 상태를 확인해 주세요. 등록 여부가 불확실하면 Dataset 목록에서 먼저 확인해 주세요.</p>
        <details><summary>오류 진단 정보</summary><p>{uploadError}</p></details>
      </div>}
    </div>}
    <div className="form-row">
      <label>모델에 적용할 Dataset
        <select aria-label="모델에 적용할 Dataset" value={datasetId}
          disabled={submitted || uploading}
          onChange={e => chooseDataset(e.target.value)}>
          <option value="">Dataset 선택</option>
          {datasets.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label>정답 레이블 (선택)
        <select aria-label="Scoring 정답 레이블" value={labelColumn}
          disabled={submitted || uploading}
          onChange={e => setLabelColumn(e.target.value)}>
          <option value="">레이블 없음</option>
          {columns.filter(col => !model.features.includes(col.name)).map(col =>
            <option key={col.name} value={col.name}>{col.name} ({col.dtype})</option>)}
        </select>
      </label>
    </div>
    {hasMoreDatasets && onLoadMoreDatasets && <button type="button" className="secondary"
      disabled={loadingMoreDatasets} onClick={() => {
        setLoadingMoreDatasets(true);
        void onLoadMoreDatasets().catch(e => setError(formatError(e)))
          .finally(() => setLoadingMoreDatasets(false));
      }}>
      {loadingMoreDatasets ? "Dataset 불러오는 중…" : "다른 Dataset 더 찾아보기"}
    </button>}
    {columnStatus === "loading" && <p role="status">Dataset의 입력 컬럼을 확인하는 중입니다.</p>}
    {columnStatus === "error" && <p role="alert">입력 컬럼을 조회하지 못했습니다: {columnError}</p>}
    {checkedColumns && missingFeatures.length > 0 &&
      <div role="alert" className="pipeline-readiness-blocked">
        <strong>필수 Feature {missingFeatures.length}개가 없습니다.</strong>
        <p>{missingFeatures.slice(0, 6).join(", ")}
          {missingFeatures.length > 6 ? " 외 " + (missingFeatures.length - 6) + "개" : ""}</p>
        <p>학습 모델과 호환되는 Dataset을 선택하세요.</p>
      </div>}
    {checkedColumns && missingFeatures.length === 0 && <p role="status">
      필수 Feature {model.features.length}개 이름을 확인했습니다. 자료형과 결측 조건은 실행 시에도 검증합니다.
    </p>}
    <p className="hint">학습 모델의 Feature 이름·자료형이 맞아야 합니다. 이상 점수는 사건 확률이 아닙니다.
      정답 레이블을 지정한 경우에만 지원되는 순위 지표를 계산합니다.</p>
    {datasetId === model.dataset_id && <p className="hint">
      학습 시 사용한 Dataset과 ID가 같습니다. Dataset 판본을 확인하세요.
      동일 학습 판본에 적용한 점수는 독립적인 Test 성능이 아닙니다.
    </p>}
    <button disabled={!canRun || submitted || uploading || !inputReady} onClick={() => void create()}>
      {submitted ? "점수 계산 요청 중…" : "이 Dataset으로 모델 사용하기 →"}
    </button>
    <details className="pipeline-scoring-history" onToggle={event => {
      setHistoryOpen(event.currentTarget.open);
      if (event.currentTarget.open) {
        setHistoryError("");
        void refresh().catch(cause => setHistoryError(formatError(cause)));
      }
    }}><summary>이 모델의 이전 점수 계산 보기</summary>
    <div className="steps">
      {historyError && <p role="alert">이전 점수 계산 이력을 확인하지 못했습니다: {historyError}</p>}
      <strong>이 모델의 Scoring 이력</strong>
      {available.map(item => <button key={item.id} className="secondary"
        onClick={() => void selectHistory(item.id)}>
        {item.id.slice(0, 8)} · {jobStatusLabel(item.status)}
      </button>)}
      {available.length === 0 && <p className="hint">현재 조회한 이력에 이 모델의 Scoring Job이 없습니다.</p>}
      {nextCursor && <button className="secondary"
        onClick={() => void loadMore().catch(e => setHistoryError(formatError(e)))}>
        이전 Scoring 더 보기
      </button>}
    </div>
    </details>

  </section>;
}
