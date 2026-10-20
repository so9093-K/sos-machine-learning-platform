import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import { MLScoringPanel } from "./MLScoringPanel";
import { MLTrackingStatus } from "./MLTrackingStatus";
import type {
  Dataset, DatasetColumns, MLTrainingJob, MLTrainingJobSummary, ModelRevision,
} from "./types";

const NUMERIC = /^(?:U?Int(?:8|16|32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

type Props = {
  datasets: Dataset[];
  selectedId: string;
  onSelect: (id: string) => void;
  canTrain: boolean;
  canCancel: boolean;
};

function statistic(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR") : "—";
}

/** 데이터 선택부터 비동기 학습·불변 결과 조회까지 같은 공개 API를 사용한다. */
export function MLWorkbench({ datasets, selectedId, onSelect, canTrain, canCancel }: Props) {
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [features, setFeatures] = useState<string[]>([]);
  const [nEstimators, setNEstimators] = useState(100);
  const [maxSamples, setMaxSamples] = useState("auto");
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
    setSelectedJob(null);
    setModel(null);
    setCompareJobId("");
    setComparisonModel(null);
    setColumns([]);
    setFeatures([]);
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
            void reloadHistory().catch(error => { if (alive) setError(formatError(error)); });
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
    if (!canTrain || !selectedId || features.length < 1 || submitting) return;
    const requestedDataset = selectedId;
    const requestedSelection = ++selectionRequest.current;
    setSubmitting(true);
    setError("");
    try {
      const result = await submitJob<MLTrainingJob>("/api/ml/train/jobs", {
        dataset_id: selectedId,
        features,
        params: { n_estimators: nEstimators, max_samples: maxSamples === "auto"
          ? "auto" : Number(maxSamples), max_features: 1.0, bootstrap: false, random_state: 42 },
      });
      // 다른 Dataset/Job으로 이동한 사이 도착한 응답은 현재 화면을 바꾸지 않는다.
      if (currentDataset.current === requestedDataset &&
          selectionRequest.current === requestedSelection) {
        setSelectedJob(result);
        setModel(null);
      }
      void reloadHistory().catch(error => setError(formatError(error)));
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
      await reloadHistory();
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

  const visibleHistory = history.filter(item => !selectedId || item.dataset_id === selectedId);
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
  const sameInput = Boolean(model && comparisonModel &&
    model.dataset_id === comparisonModel.dataset_id &&
    model.dataset_version === comparisonModel.dataset_version &&
    JSON.stringify(model.features) === JSON.stringify(comparisonModel.features) &&
    (trainingFingerprint(model) === trainingFingerprint(comparisonModel)));

  const parameterKeys = ["n_estimators", "max_samples", "max_features",
    "bootstrap", "random_state"];
  const parameterText = (revision: ModelRevision, key: string): string =>
    String(revision.params[key] ?? "—");

  return <div className="job-list">
    {error && <section className="panel" role="alert">
      <strong>ML 작업을 처리하지 못했습니다.</strong> {error}
      <button className="ghost" onClick={() => setError("")}>닫기</button>
    </section>}
    <section className="panel">
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
        <strong>학습에 사용할 숫자형 Feature ({features.length}개)</strong>
        {!selectedId && <p className="hint">Dataset을 선택하면 입력 스키마를 조회합니다.</p>}
        {loadingColumns && <p role="status" className="hint">스키마를 조회하고 있습니다.</p>}
        {selectedId && !loadingColumns && numericColumns.length === 0 &&
          <p className="hint">사용 가능한 숫자형 Feature가 없습니다. 데이터 준비에서 숫자형 Feature를 생성하세요.</p>}
        <div className="form-row">{numericColumns.map(column => <label key={column.name}>
          <input type="checkbox" checked={features.includes(column.name)}
            disabled={!canTrain}
            onChange={event => setFeatures(current => event.target.checked
              ? [...current, column.name] : current.filter(name => name !== column.name))} />
          {column.name} <small>{column.dtype}</small>
        </label>)}</div>
      </div>
      <p className="hint">숫자형 Feature를 선택하세요. Worker가 전체 Dataset을 검사한 뒤 메모리 예산 안에서 학습 행을 선택합니다. 전체 입력이 학습에 사용되지 않을 수 있으며 결측·비유한값·정밀도 오류는 실패로 처리합니다. 학습 점수는 독립 검증 성능이 아닙니다.</p>
      <div className="actions">
        <button disabled={!canTrain || submitting || !selectedId || features.length === 0 ||
          !Number.isInteger(nEstimators) || nEstimators < 1 || nEstimators > 2000}
          onClick={() => void startTraining()}>
          {submitting ? "Job 접수 중…" : "학습 Job 제출"}
        </button>
      </div>
    </section>

    <section className="panel">
      <div className="panel-head"><div><h3>ML 학습 이력</h3>
        <p>이동·새로고침 후에도 Job 상태와 모델 판본을 다시 조회할 수 있습니다.</p>
      </div><button className="secondary" onClick={() => void reloadHistory().catch(e => setError(formatError(e)))}>
        목록 새로고침</button></div>
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
    </section>
    {selectedJob && <section className="panel" aria-label="ML 실행 결과">
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
        <p className="hint">Dataset 판본·Feature·Seed·모델 설정을 복사합니다. 실행 예산이나 Runtime이 바뀌면 실제 학습 입력은 달라질 수 있습니다.</p>
      </div>}
      {selectedJob.status === "Succeeded" &&
        <MLTrackingStatus key={selectedJob.id} jobId={selectedJob.id} />}
      {model && <div className="result">
        <h4>Model Revision · {model.id}</h4>
        <p className="hint">입력 판본: {model.dataset_version} · Feature: {model.features.join(", ")}</p>
        {selectedJob?.result && selectedJob.result.training_selection && <p className="hint">
          원본 {statistic(selectedJob.result.source_rows)}행 · 학습에 사용한 입력
          {" "}{statistic(selectedJob.result.training_rows)}행 ·
          {" "}{typeof selectedJob.result.training_selection === "object" &&
            "method" in selectedJob.result.training_selection &&
            selectedJob.result.training_selection.method === "all_rows"
              ? "전체 입력" : "Feature 값 기반 결정적 부분집합 (대표성 보장 안 됨)"}
        </p>}
        <div className="cards">
          <div className="stat"><span>학습 점수 대상</span><strong>{statistic(model.training_score_summary.score_count)}행</strong></div>
          <div className="stat"><span>학습 기준 이상 후보</span><strong>{statistic(model.training_score_summary.predicted_anomalies)}건</strong></div>
          <div className="stat"><span>학습 점수 평균</span><strong>{statistic(model.training_score_summary.score_mean)}</strong></div>
        </div>
        <p className="hint">이 수치는 학습 데이터의 설명적 요약이며 검증 Precision·Recall·최적 임계값을 의미하지 않습니다. Scoring·정책·평가·설명은 아래에서 별도 결과로 생성하며, MLflow 추적 상태는 운영자 설정 시 표시합니다.</p>
        <MLScoringPanel key={model.id} model={model} datasets={datasets}
          canRun={canTrain} canCancel={canCancel} />
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
              {sameInput
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
