import { useEffect, useMemo, useState } from "react";
import { formatError, request, submitJob } from "./api";
import type {
  Dataset, DatasetColumns, MLTrainingJob, MLTrainingJobPage, MLTrainingJobSummary, ModelRevision,
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
  const [history, setHistory] = useState<MLTrainingJobSummary[]>([]);
  const [selectedJob, setSelectedJob] = useState<MLTrainingJob | null>(null);
  const [model, setModel] = useState<ModelRevision | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const numericColumns = useMemo(
    () => columns.filter(column => NUMERIC.test(column.dtype)), [columns],
  );

  async function reloadHistory() {
    const page = await request<MLTrainingJobPage>("/api/ml/train/jobs?limit=100");
    setHistory(page.items);
  }

  useEffect(() => {
    let alive = true;
    void request<MLTrainingJobPage>("/api/ml/train/jobs?limit=100").then(page => {
      if (alive) setHistory(page.items);
    }).catch(error => { if (alive) setError(formatError(error)); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    setSelectedJob(null);
    setModel(null);
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
          if (TERMINAL.has(next.status)) void reloadHistory().catch(() => {});
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

  async function startTraining() {
    if (!canTrain || !selectedId || features.length < 1 || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await submitJob<MLTrainingJob>("/api/ml/train/jobs", {
        dataset_id: selectedId,
        features,
        params: { n_estimators: nEstimators, max_samples: maxSamples === "auto"
          ? "auto" : Number(maxSamples), max_features: 1.0, bootstrap: false, random_state: 42 },
      });
      setSelectedJob(result);
      setModel(null);
      void reloadHistory().catch(() => {});
    } catch (error) {
      setError(formatError(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function selectJob(id: string) {
    setError("");
    try {
      setSelectedJob(await request<MLTrainingJob>(`/api/ml/train/jobs/${encodeURIComponent(id)}`));
    } catch (error) { setError(formatError(error)); }
  }

  async function cancelJob() {
    if (!selectedJob || !canCancel) return;
    setError("");
    try {
      const item = await request<MLTrainingJob>(
        `/api/ml/train/jobs/${encodeURIComponent(selectedJob.id)}/cancel`,
        { method: "POST" },
      );
      setSelectedJob(item);
    } catch (error) { setError(formatError(error)); }
  }

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
        <strong>학습에 사용할 숫자형 Feature ({features.length} / 32)</strong>
        {!selectedId && <p className="hint">Dataset을 선택하면 입력 스키마를 조회합니다.</p>}
        {loadingColumns && <p role="status" className="hint">스키마를 조회하고 있습니다.</p>}
        {selectedId && !loadingColumns && numericColumns.length === 0 &&
          <p className="hint">사용 가능한 숫자형 Feature가 없습니다. 데이터 준비에서 숫자형 Feature를 생성하세요.</p>}
        <div className="form-row">{numericColumns.map(column => <label key={column.name}>
          <input type="checkbox" checked={features.includes(column.name)}
            disabled={!canTrain || (!features.includes(column.name) && features.length >= 32)}
            onChange={event => setFeatures(current => event.target.checked
              ? [...current, column.name] : current.filter(name => name !== column.name))} />
          {column.name} <small>{column.dtype}</small>
        </label>)}</div>
      </div>
      <p className="hint">첫 학습 Runtime은 숫자형·최대 50,000행·32 Feature로 제한하며, 결측·NaN·Infinity는 학습 실패로 처리합니다. 학습 데이터의 점수 분포는 독립 검증 성능이 아닙니다.</p>
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
        {history.filter(item => !selectedId || item.dataset_id === selectedId).map(item =>
          <button className="secondary" key={item.id} onClick={() => void selectJob(item.id)}
            aria-pressed={selectedJob?.id === item.id}>
            {item.id.slice(0, 8)} · {item.status} · {new Date(item.created_at).toLocaleString("ko-KR")}
          </button>)}
        {history.length === 0 && <p className="muted">현재 조회된 학습 Job이 없습니다.</p>}
      </div>
    </section>
    {selectedJob && <section className="panel" aria-label="ML 실행 결과">
      <div className="panel-head"><div><h3>학습 Job 상세</h3>
        <p>{selectedJob.id} · {selectedJob.status}</p>
      </div>
        {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
          <button className="secondary" onClick={() => void cancelJob()}>취소 요청</button>}
      </div>
      {selectedJob.status === "Running" && <p role="status">실행 중입니다. 정확한 진행률은 제공되지 않습니다.</p>}
      {selectedJob.status === "Failed" && <p role="alert">
        {selectedJob.error ?? "학습에 실패했습니다."} · 오류 코드 {selectedJob.error_code ?? "미확인"}
      </p>}
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소가 요청됐습니다. 종료가 확인될 때까지 기다립니다.</p>}
      {model && <div className="result">
        <h4>Model Revision · {model.id}</h4>
        <p className="hint">입력 판본: {model.dataset_version} · Feature: {model.features.join(", ")}</p>
        <div className="cards">
          <div className="stat"><span>학습 점수 대상</span><strong>{statistic(model.training_score_summary.score_count)}행</strong></div>
          <div className="stat"><span>학습 기준 이상 후보</span><strong>{statistic(model.training_score_summary.predicted_anomalies)}건</strong></div>
          <div className="stat"><span>학습 점수 평균</span><strong>{statistic(model.training_score_summary.score_mean)}</strong></div>
        </div>
        <p className="hint">이 수치는 학습 데이터에서 계산한 설명적 요약으로, 검증 Precision·Recall·최적 임계값을 의미하지 않습니다. 설명·독립 평가·MLflow 추적은 별도 구현 대상입니다.</p>
      </div>}
    </section>}
  </div>;
}
