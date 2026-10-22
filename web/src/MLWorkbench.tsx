import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import { MLTrackingStatus } from "./MLTrackingStatus";
import { FeatureSelector } from "./FeatureSelector";
import type {
  Dataset, DatasetColumns, MLTrainingJob, MLTrainingJobSummary, ModelRevision,
} from "./types";

const NUMERIC = /^(?:U?Int(?:8|16|32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

type Props = {
  datasets: Dataset[];
  canTrain: boolean;
  canCancel: boolean;
  view: "models" | "training";
  onViewChange: (view: "models" | "training") => void;
  onOpenModel: (id: string) => void;
};

function statistic(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR") : "—";
}

function trainingSelectionLabel(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "학습 입력 근거 미확인";
  if ("method" in value && value.method === "all_rows") return "전체 입력";
  if ("method" in value && (value.method === "canonical_multiset_v1" ||
      value.method === "row_priority_bottom_k_v1")) {
    const rows = "requested_rows" in value ? value.requested_rows : null;
    const seed = "seed" in value ? value.seed : null;
    const scope = value.method === "canonical_multiset_v1"
      ? "순서 독립 부분집합" : "기존 부분집합 (스캔 순서 영향 가능)";
    return `${scope} ${statistic(rows)}행 · 선택 Seed ${statistic(seed)} · 대표성 보장 없음`;
  }
  return "학습 입력 선택 방식 미확인";
}

/** 데이터 선택부터 비동기 학습·불변 결과 조회까지 같은 공개 API를 사용한다. */
export function MLWorkbench({ datasets, canTrain, canCancel, view: modelView, onViewChange, onOpenModel }: Props) {
  const [selectedId, onSelect] = useState("");
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [features, setFeatures] = useState<string[]>([]);
  const [historyDatasetFilter, setHistoryDatasetFilter] = useState("");
  const [nEstimators, setNEstimators] = useState(100);
  const [maxSamples, setMaxSamples] = useState("auto");
  const [inputMode, setInputMode] = useState<"all" | "sample">("all");
  const [inputRows, setInputRows] = useState(100000);
  const [inputSeed, setInputSeed] = useState(42);
  const [loadingColumns, setLoadingColumns] = useState(false);
  const {
    items: history, nextCursor: historyCursor, refresh: reloadHistory, loadMore: loadOlderHistory,
  } = usePagedJobs<MLTrainingJobSummary>("/api/ml/train/jobs");
  const [selectedJob, setSelectedJob] = useState<MLTrainingJob | null>(null);
  const [model, setModel] = useState<ModelRevision | null>(null);
  const [compareJobId, setCompareJobId] = useState("");
  const [comparisonModel, setComparisonModel] = useState<ModelRevision | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const currentDataset = useRef(selectedId);
  currentDataset.current = selectedId;
  const selectionRequest = useRef(0);

  const numericColumns = useMemo(
    () => columns.filter(column => NUMERIC.test(column.dtype)), [columns],
  );

  useEffect(() => {
    let alive = true;
    void reloadHistory().catch(error => { if (alive) setError(formatError(error)); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    selectionRequest.current += 1;
    // 학습 입력 Dataset을 바꾸어도 이미 선택한 모델 판본은 별개의 조회 결과다.
    setColumns([]);
    setFeatures([]);
    setLoadingColumns(false);
    setError("");
    if (!selectedId) return () => { alive = false; };
    setLoadingColumns(true);
    void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(selectedId)}/columns`)
      .then(result => { if (alive) setColumns(result.columns); })
      .catch(error => { if (alive) setError(formatError(error)); })
      .finally(() => { if (alive) setLoadingColumns(false); });
    return () => { alive = false; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedJob || TERMINAL.has(selectedJob.status)) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await request<MLTrainingJob>(
          `/api/ml/train/jobs/${encodeURIComponent(selectedJob!.id)}`,
        );
        if (alive) {
          setSelectedJob(next);
          if (TERMINAL.has(next.status)) {
            void reloadHistory().catch(() => {
              if (alive) setNotice("학습 작업의 상태는 확인됐지만 이력 목록 갱신에 실패했습니다.");
            });
          }
        }
      } catch (error) {
        if (alive) setError(formatError(error));
      } finally {
        if (alive) timer = setTimeout(() => void poll(), 2500);
      }
    }
    timer = setTimeout(() => void poll(), 1500);
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [selectedJob?.id, selectedJob?.status]);

  useEffect(() => {
    let alive = true;
    setModel(null);
    const identifier = selectedJob?.result?.model_revision_id;
    if (selectedJob?.status !== "Succeeded" || typeof identifier !== "string") {
      return () => { alive = false; };
    }
    void request<ModelRevision>(`/api/ml/models/${encodeURIComponent(identifier)}`)
      .then(result => { if (alive) setModel(result); })
      .catch(error => { if (alive) setError(formatError(error)); });
    return () => { alive = false; };
  }, [selectedJob?.status, selectedJob?.result?.model_revision_id]);

  useEffect(() => {
    let alive = true;
    setComparisonModel(null);
    const baseline = history.find(item => item.id === compareJobId);
    const revisionId = baseline?.result?.model_revision_id;
    if (compareJobId === selectedJob?.id || !model || typeof revisionId !== "string") {
      return () => { alive = false; };
    }
    void request<ModelRevision>(`/api/ml/models/${encodeURIComponent(revisionId)}`)
      .then(result => { if (alive) setComparisonModel(result); })
      .catch(error => { if (alive) setError(formatError(error)); });
    return () => { alive = false; };
  }, [compareJobId, history, model?.id, selectedJob?.id]);

  async function startTraining() {
    if (!canTrain || !selectedId || features.length < 1 || submitting ||
        (inputMode === "sample" && (!Number.isSafeInteger(inputRows) || inputRows < 2 ||
          !Number.isInteger(inputSeed) || inputSeed < 0 || inputSeed > 4294967295))) return;
    const requestedDataset = selectedId;
    const requestedSelection = ++selectionRequest.current;
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await submitJob<MLTrainingJob>("/api/ml/train/jobs", {
        dataset_id: selectedId,
        features,
        training_input: inputMode === "all" ? { mode: "all" } :
          { mode: "sample", rows: inputRows, seed: inputSeed },
        params: { n_estimators: nEstimators, max_samples: maxSamples === "auto"
          ? "auto" : Number(maxSamples), max_features: 1.0, bootstrap: false, random_state: 42 },
      });
      // 다른 Dataset/Job으로 이동한 사이 도착한 응답은 현재 화면을 바꾸지 않는다.
      if (currentDataset.current === requestedDataset &&
          selectionRequest.current === requestedSelection) {
        setSelectedJob(result);
        setModel(null);
        setHistoryDatasetFilter("");
        onViewChange("models");
      }
      void reloadHistory().catch(() =>
        setNotice("학습 Job은 접수됐지만 이력 목록을 갱신하지 못했습니다. 다시 조회할 수 있습니다."));
    } catch (error) {
      setError(formatError(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function selectJob(id: string) {
    const requestedDataset = selectedId;
    const requestedSelection = ++selectionRequest.current;
    setError("");
    try {
      const result = await request<MLTrainingJob>(`/api/ml/train/jobs/${encodeURIComponent(id)}`);
      if (currentDataset.current === requestedDataset &&
          selectionRequest.current === requestedSelection) {
        setSelectedJob(result);
      }
    } catch (error) {
      if (selectionRequest.current === requestedSelection) setError(formatError(error));
    }
  }

  async function forkTraining() {
    if (!canTrain || submitting || !selectedJob || selectedJob.status !== "Succeeded") return;
    const requestedDataset = selectedId;
    const requestedSelection = ++selectionRequest.current;
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const result = await submitJob<MLTrainingJob>(
        `/api/ml/train/jobs/${encodeURIComponent(selectedJob.id)}/fork`, { params: null },
      );
      if (currentDataset.current === requestedDataset &&
          selectionRequest.current === requestedSelection) {
        setSelectedJob(result);
        setModel(null);
        setCompareJobId("");
      }
      try { await reloadHistory(); }
      catch { setNotice("Fork Job은 접수됐지만 이력 목록을 갱신하지 못했습니다."); }
    } catch (error) { setError(formatError(error)); }
    finally { setSubmitting(false); }
  }

  async function cancelJob() {
    if (!selectedJob || !canCancel) return;
    setError("");
    try {
      const item = await request<MLTrainingJob>(
        `/api/ml/train/jobs/${encodeURIComponent(selectedJob.id)}/cancel`,
        { method: "POST" },
      );
      setSelectedJob(current => current?.id === item.id ? item : current);
    } catch (error) { setError(formatError(error)); }
  }

  const visibleHistory = history.filter(item =>
    !historyDatasetFilter || item.dataset_id === historyDatasetFilter);
  const comparisonCandidates = visibleHistory.filter(item =>
    item.id !== selectedJob?.id &&
    item.status === "Succeeded" &&
    typeof item.result?.model_revision_id === "string");
  const trainingFingerprint = (revision: ModelRevision): string | null => {
    const selection = revision.runtime.training_selection;
    if (!selection || typeof selection !== "object" || Array.isArray(selection) ||
        !("selection_sha256" in selection)) return null;
    return typeof selection.selection_sha256 === "string" ? selection.selection_sha256 : null;
  };
  const currentInputFingerprint = model ? trainingFingerprint(model) : null;
  const comparisonInputFingerprint = comparisonModel ? trainingFingerprint(comparisonModel) : null;
  const inputEvidenceMissing = Boolean(model && comparisonModel &&
    (currentInputFingerprint === null || comparisonInputFingerprint === null));
  const sameInput = Boolean(model && comparisonModel &&
    model.dataset_id === comparisonModel.dataset_id &&
    model.dataset_version === comparisonModel.dataset_version &&
    JSON.stringify(model.features) === JSON.stringify(comparisonModel.features) &&
    currentInputFingerprint !== null &&
    currentInputFingerprint === comparisonInputFingerprint);

  const parameterKeys = ["n_estimators", "max_samples", "max_features",
    "bootstrap", "random_state"];
  const parameterText = (revision: ModelRevision, key: string): string =>
    String(revision.params[key] ?? "—");

  return <div className="job-list">
    {notice && <section className="alert success" role="status">
      {notice}<button type="button" onClick={() => setNotice("")} aria-label="ML 알림 닫기">×</button>
    </section>}
    {error && <section className="panel" role="alert">
      <strong>ML 작업을 처리하지 못했습니다.</strong> {error}
      <button className="ghost" onClick={() => setError("")}>닫기</button>
    </section>}
    <div className="panel model-view-controls">
      <div><h3>모델 판본과 학습</h3>
        <p className="hint">모델 결과를 조회하는 일과 새 학습을 시작하는 일은 별개의 작업입니다.</p></div>
      <div className="model-view-switch" role="group" aria-label="모델 작업 선택">
        <button type="button" className={modelView === "models" ? "" : "secondary"}
          aria-pressed={modelView === "models"} onClick={() => onViewChange("models")}>학습 이력·모델 판본</button>
        <button type="button" className={modelView === "training" ? "" : "secondary"}
          aria-pressed={modelView === "training"} onClick={() => onViewChange("training")}>새 모델 학습</button>
      </div>
    </div>
    {modelView === "training" && <section className="panel">
      <div className="panel-head"><div>
        <h3>Isolation Forest 실험</h3>
        <p>숫자형 결측 없는 입력으로 비동기 학습합니다. Dataset 판본과 모델 결과는 변경할 수 없습니다.</p>
      </div></div>
      <div className="form-row">
        <label>학습 Dataset
          <select aria-label="ML 학습 Dataset" value={selectedId} onChange={event => onSelect(event.target.value)}>
            <option value="">선택하세요</option>
            {datasets.map(dataset => <option value={dataset.id} key={dataset.id}>{dataset.name}</option>)}
          </select>
        </label>
        <label>트리 개수
          <input type="number" aria-label="Isolation Forest 트리 개수"
            min={1} max={2000} value={nEstimators}
            onChange={event => setNEstimators(Number(event.target.value))} />
        </label>
        <label>트리별 표본 수
          <select aria-label="트리별 학습 표본 수" value={maxSamples} onChange={event => setMaxSamples(event.target.value)}>
            <option value="auto">자동 (최대 256행)</option>
            <option value="16">16행</option>
            <option value="64">64행</option>
            <option value="256">256행</option>
            <option value="1024">1,024행</option>
          </select>
        </label>
      </div>
      <div className="steps">
        <strong>학습 입력 범위</strong>
        <div className="form-row">
          <label>입력 선택 방식
            <select aria-label="학습 입력 선택 방식" value={inputMode}
              onChange={event => setInputMode(event.target.value as "all" | "sample")}>
              <option value="all">전체 Dataset</option>
              <option value="sample">명시적 무작위 부분집합</option>
            </select>
          </label>
          {inputMode === "sample" && <>
            <label>학습 입력 행 수
              <input aria-label="학습 표본 행 수" type="number" min={2} step={1}
                value={inputRows} onChange={event => setInputRows(Number(event.target.value))} />
            </label>
            <label>입력 선택 Seed
              <input aria-label="학습 입력 선택 Seed" type="number" min={0} max={4294967295}
                step={1} value={inputSeed}
                onChange={event => setInputSeed(Number(event.target.value))} />
            </label>
          </>}
        </div>
        <p className="hint">{inputMode === "all"
          ? "Dataset의 모든 행을 학습에 사용합니다. Worker 예산을 넘으면 자동 축소하지 않고 실패합니다."
          : "지정한 행 수와 Seed로 전체 Dataset에서 관측을 선택합니다. 같은 입력의 Feature 값과 중복 횟수라면 스캔 순서에 무관하게 같은 표본을 얻습니다. 요청 행 수보다 Dataset이 작거나 예산이 부족하면 실패합니다."}</p>
      </div>
      <div className="steps">
        <strong>학습에 사용할 숫자형 Feature ({features.length}개)</strong>
        {!selectedId && <p className="hint">Dataset을 선택하면 입력 스키마를 조회합니다.</p>}
        {loadingColumns && <p role="status" className="hint">스키마를 조회하고 있습니다.</p>}
        {selectedId && !loadingColumns && numericColumns.length === 0 &&
          <p className="hint">사용 가능한 숫자형 Feature가 없습니다. 데이터 준비에서 숫자형 Feature를 생성하세요.</p>}
        <FeatureSelector key={selectedId} columns={numericColumns} selected={features}
          disabled={!canTrain} onChange={setFeatures} />
      </div>
      <p className="hint">숫자형 Feature를 선택하세요. 학습 입력은 위에서 선택한 정책에 따르며, 실행 예산에 맞지 않으면 실패합니다. 결측·비유한값·정밀도 오류는 실패로 처리합니다. 학습 점수는 독립 검증 성능이 아닙니다.</p>
      <div className="actions">
        <button disabled={!canTrain || submitting || !selectedId || features.length === 0 ||
          !Number.isInteger(nEstimators) || nEstimators < 1 || nEstimators > 2000 ||
          (inputMode === "sample" && (!Number.isSafeInteger(inputRows) || inputRows < 2 ||
            !Number.isInteger(inputSeed) || inputSeed < 0 || inputSeed > 4294967295))}
          onClick={() => void startTraining()}>
          {submitting ? "Job 접수 중…" : "학습 Job 제출"}
        </button>
      </div>
    </section>}

    {modelView === "models" && <section className="panel">
      <div className="panel-head"><div><h3>학습 이력·모델 판본</h3>
        <p>이동·새로고침 후에도 Job 상태와 모델 판본을 다시 조회할 수 있습니다.</p>
      </div><button className="secondary" onClick={() => void reloadHistory().catch(e => setError(formatError(e)))}>
        목록 새로고침</button></div>
      <div className="model-history-filter">
        <label>원본 Dataset
          <select aria-label="모델 이력 Dataset 필터" value={historyDatasetFilter}
            onChange={event => {
              setHistoryDatasetFilter(event.target.value);
              setSelectedJob(null);
              setModel(null);
              setCompareJobId("");
              setComparisonModel(null);
            }}>
            <option value="">모든 Dataset</option>
            {datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
      </div>
      <div className="job-list">
        {visibleHistory.map(item =>
          <button className="secondary" key={item.id} onClick={() => void selectJob(item.id)}
            aria-pressed={selectedJob?.id === item.id}>
            {item.id.slice(0, 8)} · {item.status} · {new Date(item.created_at).toLocaleString("ko-KR")}
          </button>)}
        {visibleHistory.length === 0 && <p className="muted">현재 조회된 학습 Job이 없습니다.</p>}
        {historyCursor && <button className="secondary"
          onClick={() => void loadOlderHistory().catch(error => setError(formatError(error)))}>
          이전 학습 더 보기
        </button>}
      </div>
    </section>}
    {modelView === "models" && selectedJob && <section className="panel" aria-label="ML 실행 결과">
      <div className="panel-head"><div><h3>학습 Job 상세</h3>
        <p>{selectedJob.id} · {selectedJob.status}</p>
      </div>
        {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
          !selectedJob.cancel_requested &&
          <button className="secondary" onClick={() => void cancelJob()}>취소 요청</button>}
      </div>
      {selectedJob.status === "Running" && <p role="status">실행 중입니다. 정확한 진행률은 제공되지 않습니다.</p>}
      {selectedJob.status === "Failed" && <p role="alert">
        {selectedJob.error ?? "학습에 실패했습니다."} · 오류 코드 {selectedJob.error_code ?? "미확인"}
      </p>}
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소가 요청됐습니다. 종료가 확인될 때까지 기다립니다.</p>}
      {selectedJob.status === "Succeeded" && <div className="actions">
        <button className="secondary" disabled={!canTrain || submitting}
          onClick={() => void forkTraining()}>
          {submitting ? "Fork 제출 중…" : "동일 조건으로 Fork·Reproduce"}
        </button>
        <p className="hint">Dataset 판본·Feature·입력 선택 정책·선택 알고리즘 버전·Seed·모델 설정을 복사합니다. 과거 방식은 스캔 순서에 영향을 받으며, Runtime 변경에 따른 결과 차이는 별도로 확인해야 합니다.</p>
      </div>}
      {selectedJob.status === "Succeeded" &&
        <MLTrackingStatus key={selectedJob.id} jobId={selectedJob.id} />}
      {model && <div className="result">
        <h4>Model Revision · {model.id}</h4>
        <p className="hint">입력 판본: {model.dataset_version} · Feature: {model.features.join(", ")}</p>
        {selectedJob?.result && selectedJob.result.training_selection != null && <p className="hint">
          원본 {statistic(selectedJob.result.source_rows)}행 · 학습에 사용한 입력
          {" "}{statistic(selectedJob.result.training_rows)}행 ·
          {" "}{trainingSelectionLabel(selectedJob.result.training_selection)}
        </p>}
        <div className="cards">
          <div className="stat"><span>학습 점수 대상</span><strong>{statistic(model.training_score_summary.score_count)}행</strong></div>
          <div className="stat"><span>학습 기준 이상 후보</span><strong>{statistic(model.training_score_summary.predicted_anomalies)}건</strong></div>
          <div className="stat"><span>학습 점수 평균</span><strong>{statistic(model.training_score_summary.score_mean)}</strong></div>
        </div>
        <p className="hint">이 수치는 학습 데이터의 설명적 요약이며 검증 Precision·Recall·최적 임계값을 의미하지 않습니다. Scoring·정책·평가·설명은 아래에서 별도 결과로 생성하며, MLflow 추적 상태는 운영자 설정 시 표시합니다.</p>
        <div className="actions"><button type="button" className="secondary"
          onClick={() => onOpenModel(model.id)}>모델 판본 상세·점수화로 이동 →</button></div>
        <div className="steps">
          <h4>학습 설정 비교</h4>
          <p className="hint">이미 완료한 모델의 입력 조건과 설정만 비교합니다. 재학습하거나 새로운 임계값을 적용하지 않습니다.</p>
          <label>비교할 학습 Job
            <select aria-label="비교할 학습 Job" value={compareJobId}
              onChange={event => setCompareJobId(event.target.value)}>
              <option value="">비교 대상 선택</option>
              {comparisonCandidates.map(item => <option key={item.id} value={item.id}>
                {item.id.slice(0, 8)} · {new Date(item.created_at).toLocaleString("ko-KR")}
              </option>)}
            </select>
          </label>
          {comparisonModel && <>
            <p className="hint" role="status">
              {inputEvidenceMissing
                ? "학습 입력 지문이 없는 과거 모델이 포함되어 실제 입력의 동일성을 확인할 수 없습니다."
                : sameInput
                  ? "데이터 판본·Feature 순서·실제 학습 입력 지문이 같습니다. 설정 차이를 확인할 수 있습니다."
                  : "입력 판본·Feature 또는 실제 학습 입력이 다릅니다. 점수·경보량을 직접 비교할 수 없습니다."}
            </p>
            <table>
              <caption>두 모델의 학습 설정 비교 (성능 순위 아님)</caption>
              <thead><tr><th scope="col">항목</th><th scope="col">현재 모델</th>
                <th scope="col">비교 모델</th></tr></thead>
              <tbody>
                {parameterKeys.map(key => <tr key={key}><th scope="row">{key}</th>
                  <td>{parameterText(model, key)}</td>
                  <td>{parameterText(comparisonModel, key)}</td></tr>)}
                <tr><th scope="row">학습 행 수</th>
                  <td>{statistic(model.training_score_summary.score_count)}</td>
                  <td>{statistic(comparisonModel.training_score_summary.score_count)}</td></tr>
                <tr><th scope="row">scikit-learn</th>
                  <td>{String(model.runtime.scikit_learn ?? "—")}</td>
                  <td>{String(comparisonModel.runtime.scikit_learn ?? "—")}</td></tr>
              </tbody>
            </table>
            <p className="hint">학습 점수 평균·이상 후보 수는 독립 검증 성능이 아니며, 같은 데이터를 사용해도 서로 다른 모델의 점수를 성능 순위로 해석하지 않습니다.</p>
          </>}
        </div>
      </div>}
    </section>}
  </div>;
}
