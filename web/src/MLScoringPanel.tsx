import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import type {
  Dataset, DatasetColumns, MLScoringJob, MLScoringJobSummary, ModelRevision, ScoringResult,
} from "./types";

type Props = {
  model: ModelRevision;
  datasets: Dataset[];
  canRun: boolean;
  canCancel: boolean;
  onOpenResult: (id: string) => void;
};

const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

/** 학습된 모델을 다른 Dataset에 적용하는 독립 Scoring 경험. */
export function MLScoringPanel({ model, datasets, canRun, canCancel, onOpenResult }: Props) {
  const [datasetId, setDatasetId] = useState("");
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [labelColumn, setLabelColumn] = useState("");
  const [columnStatus, setColumnStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [columnError, setColumnError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [selectedJob, setSelectedJob] = useState<MLScoringJob | null>(null);
  const [result, setResult] = useState<ScoringResult | null>(null);
  const [error, setError] = useState("");
  const { items: history, nextCursor, refresh, loadMore } =
    usePagedJobs<MLScoringJobSummary>("/api/ml/scoring/jobs");

  useEffect(() => {
    let alive = true;
    void refresh().catch(e => { if (alive) setError(formatError(e)); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    setColumns([]);
    setLabelColumn("");
    setColumnError("");
    setColumnStatus(datasetId ? "loading" : "idle");
    if (datasetId) {
      void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(datasetId)}/columns`)
        .then(data => {
          if (alive) { setColumns(data.columns); setColumnStatus("ready"); }
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
          if (TERMINAL.has(next.status)) {
            void refresh().catch(e => { if (alive) setError(formatError(e)); });
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
  }, [selectedJob?.id, selectedJob?.status]);

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

  const missingFeatures = columnStatus === "ready"
    ? model.features.filter(feature => !columns.some(col => col.name === feature)) : [];
  const inputReady = datasetId !== "" && columnStatus === "ready" && missingFeatures.length === 0;
  async function create() {
    if (submitted || !canRun || !inputReady) return;
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
      void refresh().catch(e => setError(formatError(e)));
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
    {error && <p role="alert">{error}</p>}
    <div className="form-row">
      <label>모델에 적용할 Dataset
        <select aria-label="모델에 적용할 Dataset" value={datasetId}
          onChange={e => setDatasetId(e.target.value)}>
          <option value="">Dataset 선택</option>
          {datasets.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label>정답 레이블 (선택)
        <select aria-label="Scoring 정답 레이블" value={labelColumn}
          onChange={e => setLabelColumn(e.target.value)}>
          <option value="">레이블 없음</option>
          {columns.filter(col => !model.features.includes(col.name)).map(col =>
            <option key={col.name} value={col.name}>{col.name} ({col.dtype})</option>)}
        </select>
      </label>
    </div>
    {columnStatus === "loading" && <p role="status">Dataset의 입력 컬럼을 확인하는 중입니다.</p>}
    {columnStatus === "error" && <p role="alert">입력 컬럼을 조회하지 못했습니다: {columnError}</p>}
    {columnStatus === "ready" && missingFeatures.length > 0 &&
      <div role="alert" className="pipeline-readiness-blocked">
        <strong>필수 Feature {missingFeatures.length}개가 없습니다.</strong>
        <p>{missingFeatures.slice(0, 6).join(", ")}
          {missingFeatures.length > 6 ? " 외 " + (missingFeatures.length - 6) + "개" : ""}</p>
        <p>학습 모델과 호환되는 Dataset을 선택하세요.</p>
      </div>}
    {columnStatus === "ready" && missingFeatures.length === 0 && <p role="status">
      필수 Feature {model.features.length}개 이름을 확인했습니다. 자료형과 결측 조건은 실행 시에도 검증합니다.
    </p>}
    <p className="hint">학습 모델의 Feature 이름·자료형이 맞아야 합니다. 이상 점수는 사건 확률이 아닙니다.
      정답 레이블을 지정한 경우에만 지원되는 순위 지표를 계산합니다.</p>
    {datasetId === model.dataset_id && <p className="hint">
      이 모델의 학습에 사용한 Dataset입니다. 여기에 적용한 점수는 독립적인 Test 성능이 아닙니다.
    </p>}
    <button disabled={!canRun || submitted || !inputReady} onClick={() => void create()}>
      {submitted ? "점수 계산 요청 중…" : "이 Dataset으로 모델 사용하기 →"}
    </button>
    <details className="pipeline-scoring-history"><summary>이 모델의 이전 점수 계산 보기</summary>
    <div className="steps">
      <strong>이 모델의 Scoring 이력</strong>
      {available.map(item => <button key={item.id} className="secondary"
        onClick={() => void selectHistory(item.id)}>
        {item.id.slice(0, 8)} · {item.status}
      </button>)}
      {available.length === 0 && <p className="hint">현재 조회한 이력에 이 모델의 Scoring Job이 없습니다.</p>}
      {nextCursor && <button className="secondary"
        onClick={() => void loadMore().catch(e => setError(formatError(e)))}>
        이전 Scoring 더 보기
      </button>}
    </div>
    </details>
    {selectedJob && <div className="result">
      <h4>모델 점수 계산 · {({
        Queued: "대기 중", Running: "진행 중", Succeeded: "완료",
        Failed: "실패", Cancelled: "취소됨",
      } as Record<string, string>)[selectedJob.status] ?? selectedJob.status}</h4>
      {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
        !selectedJob.cancel_requested &&
        <button className="secondary" onClick={() => void cancel()}>Scoring 취소 요청</button>}
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소가 요청됐습니다. 종료가 확인될 때까지 기다립니다.</p>}
      {selectedJob.status === "Failed" && <p role="alert">Scoring 실패 · {selectedJob.error_code}</p>}
      {result && <div>
        <p>새로운 점수 결과가 저장됐습니다. 원래 학습·Test 결과는 변경되지 않습니다.</p>
        <div className="cards">
          <div className="stat"><span>점수화된 행</span><strong>{result.row_count}</strong></div>
          <div className="stat"><span>평균 이상 점수</span>
            <strong>{value(result.score_summary.score_mean)}</strong></div>
          <div className="stat"><span>Average Precision</span>
            <strong>{value(result.ranking_metrics?.average_precision)}</strong></div>
          <div className="stat"><span>ROC-AUC</span>
            <strong>{value(result.ranking_metrics?.roc_auc)}</strong></div>
        </div>
        <p className="hint">모델과 Scoring Dataset의 판본 및 점수 아티팩트가 보존됩니다. 위 지표는 완전한 이진 레이블과 양성·음성 사례가 모두 있을 때만 계산합니다. 임계값 정책과 판정 정확도는 별도 평가 대상입니다.</p>
        {result.dataset_id === model.dataset_id && result.dataset_version === model.dataset_version &&
          <p className="hint">학습에 사용한 동일 Dataset 판본을 점수화했습니다. 이 결과의 순위·판정 지표는 독립 검증 데이터의 성능이 아닙니다.</p>}
        <button type="button" className="secondary" onClick={() => onOpenResult(result.id)}>
          점수 결과와 판단 근거 살펴보기 →
        </button>
      </div>}
    </div>}
  </section>;
}
