import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { MLTrackingStatus } from "./MLTrackingStatus";
import { FeatureSelector } from "./FeatureSelector";
import type { Dataset, DatasetColumns, MLTrainingJob, ModelRevision } from "./types";

const NUMERIC = /^(?:U?Int(?:8|16|32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

type Props = {
  datasets: Dataset[];
  canTrain: boolean;
  canCancel: boolean;
  view: "models" | "training";
  onViewChange: (view: "models" | "training") => void;
  onOpenModel: (id: string) => void;
  initialDatasetId: string;
  onDatasetSelected: (id: string) => void;
};

function statistic(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR") : "—";
}

/** 독립 Training은 SOS 실행 계약, 모델 자산 탐색은 MLflowResearch가 책임진다. */
export function MLWorkbench({ datasets, canTrain, canCancel, onOpenModel,
  initialDatasetId, onDatasetSelected }: Props) {
  const selectedId = initialDatasetId;
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [features, setFeatures] = useState<string[]>([]);
  const [nEstimators, setNEstimators] = useState(100);
  const [maxSamples, setMaxSamples] = useState("auto");
  const [inputMode, setInputMode] = useState<"all" | "sample">("all");
  const [inputRows, setInputRows] = useState(100000);
  const [inputSeed, setInputSeed] = useState(42);
  const [loadingColumns, setLoadingColumns] = useState(false);
  const [selectedJob, setSelectedJob] = useState<MLTrainingJob | null>(null);
  const [model, setModel] = useState<ModelRevision | null>(null);
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
    selectionRequest.current += 1;
    setColumns([]);
    setFeatures([]);
    setLoadingColumns(false);
    if (!selectedId) return () => { alive = false; };
    setLoadingColumns(true);
    void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(selectedId)}/columns`)
      .then(result => { if (alive) setColumns(result.columns); })
      .catch(exc => { if (alive) setError(formatError(exc)); })
      .finally(() => { if (alive) setLoadingColumns(false); });
    return () => { alive = false; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedJob || TERMINAL.has(selectedJob.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await request<MLTrainingJob>(
          `/api/ml/train/jobs/${encodeURIComponent(selectedJob!.id)}`);
        if (active) setSelectedJob(next);
      } catch (exc) {
        if (active) setError(formatError(exc));
      } finally {
        if (active) timer = setTimeout(() => void poll(), 2500);
      }
    }
    timer = setTimeout(() => void poll(), 1500);
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [selectedJob?.id, selectedJob?.status]);

  useEffect(() => {
    let active = true;
    setModel(null);
    const id = selectedJob?.result?.model_revision_id;
    if (selectedJob?.status !== "Succeeded" || typeof id !== "string") {
      return () => { active = false; };
    }
    void request<ModelRevision>(`/api/ml/models/${encodeURIComponent(id)}`)
      .then(next => { if (active) setModel(next); })
      .catch(exc => { if (active) setError(formatError(exc)); });
    return () => { active = false; };
  }, [selectedJob?.status, selectedJob?.result?.model_revision_id]);

  async function startTraining() {
    if (!canTrain || !selectedId || features.length < 1 || submitting ||
        (inputMode === "sample" && (!Number.isSafeInteger(inputRows) || inputRows < 2 ||
          !Number.isInteger(inputSeed) || inputSeed < 0 || inputSeed > 4294967295))) return;
    const expectedDataset = selectedId;
    const revision = ++selectionRequest.current;
    setSubmitting(true); setError(""); setNotice("");
    try {
      const result = await submitJob<MLTrainingJob>("/api/ml/train/jobs", {
        dataset_id: selectedId, features,
        training_input: inputMode === "all" ? { mode: "all" } :
          { mode: "sample", rows: inputRows, seed: inputSeed },
        params: { n_estimators: nEstimators, max_samples: maxSamples === "auto"
          ? "auto" : Number(maxSamples), max_features: 1.0, bootstrap: false, random_state: 42 },
      });
      if (currentDataset.current === expectedDataset &&
          selectionRequest.current === revision) {
        setSelectedJob(result);
        setModel(null);
        setNotice("학습 Job이 접수됐습니다. 현재 화면에서 상태를 확인할 수 있습니다.");
      }
    } catch (exc) { setError(formatError(exc)); }
    finally { setSubmitting(false); }
  }

  async function cancelJob() {
    if (!selectedJob || !canCancel) return;
    setError("");
    try {
      const result = await request<MLTrainingJob>(
        `/api/ml/train/jobs/${encodeURIComponent(selectedJob.id)}/cancel`,
        { method: "POST" },
      );
      setSelectedJob(old => old?.id === result.id ? result : old);
    } catch (exc) { setError(formatError(exc)); }
  }

  async function forkTraining() {
    if (!canTrain || submitting || !selectedJob || selectedJob.status !== "Succeeded") return;
    const expectedDataset = selectedId;
    const revision = ++selectionRequest.current;
    setSubmitting(true); setError("");
    try {
      const result = await submitJob<MLTrainingJob>(
        `/api/ml/train/jobs/${encodeURIComponent(selectedJob.id)}/fork`, { params: null });
      if (currentDataset.current === expectedDataset &&
          selectionRequest.current === revision) {
        setSelectedJob(result);
        setModel(null);
      }
    } catch (exc) { setError(formatError(exc)); }
    finally { setSubmitting(false); }
  }

  return <div className="job-list">
    {notice && <section className="alert success" role="status">
      {notice}<button type="button" onClick={() => setNotice("")} aria-label="ML 알림 닫기">×</button>
    </section>}
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
          <select aria-label="ML 학습 Dataset" value={selectedId}
            onChange={event => onDatasetSelected(event.target.value)}>
            <option value="">선택하세요</option>
            {selectedId && !datasets.some(item => item.id === selectedId) &&
              <option value={selectedId}>현재 목록 외 Dataset · {selectedId}</option>}
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
    </section>
    {selectedJob && <section className="panel" aria-label="ML 학습 Job 상태">
      <div className="panel-head"><div><h3>학습 Job 상태</h3>
        <p>Job {selectedJob.id} · {selectedJob.status}</p>
      </div></div>
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소 요청 중입니다. 실제 종료 확인까지 기다립니다.</p>}
      {selectedJob.status === "Failed" && <p role="alert">
        {selectedJob.error ?? "학습 실패"} · 오류 코드 {selectedJob.error_code ?? "미확인"}
      </p>}
      {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
        !selectedJob.cancel_requested && <button type="button" className="secondary"
          onClick={() => void cancelJob()}>취소 요청</button>}
      {selectedJob.status === "Succeeded" && <>
        <MLTrackingStatus key={selectedJob.id} jobId={selectedJob.id}/>
        <button type="button" className="secondary" disabled={!canTrain || submitting}
          onClick={() => void forkTraining()}>동일 조건 Fork·Reproduce</button>
      </>}
      {model && <div className="result">
        <h4>모델 실행 근거 · {model.id}</h4>
        <p className="hint">Dataset 판본 {model.dataset_version} · 입력 Feature {model.features.join(", ")}</p>
        <p>학습 대상 {statistic(model.training_score_summary.score_count)}행 · 이상 점수 평균
          {" "}{statistic(model.training_score_summary.score_mean)} (확률·검증 성능 아님)</p>
        <button type="button" onClick={() => onOpenModel(model.id)}>불변 모델 근거 보기 →</button>
      </div>}
    </section>}
  </div>;
}
