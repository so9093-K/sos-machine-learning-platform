import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { MLTrackingStatus } from "./MLTrackingStatus";
import { FeatureSelector } from "./FeatureSelector";
import { FeastFeaturePanel } from "./FeastFeaturePanel";
import { isLocalOperatorHost } from "./operator-tools";
import type { Dataset, DatasetColumns, MLTrainingJob, ModelRevision } from "./types";
import { jobStatusLabel } from "./display-format";

const NUMERIC = /^(?:U?Int(?:8|16|32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

type Props = {
  datasets: Dataset[];
  canTrain: boolean;
  canCancel: boolean;
  canInspectOperations?: boolean;
  onOpenModel: (id: string) => void;
  initialDatasetId: string;
  registryKey: string;
  onRegistryGroupSelected: (key: string) => void;
  onDatasetSelected: (id: string) => void;
};

function statistic(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR") : "—";
}

/** 독립 Training은 SOS 실행 계약, 모델 자산 탐색은 MLflowResearch가 책임진다. */
export function MLWorkbench({ datasets, canTrain, canCancel, canInspectOperations = false,
  onOpenModel, initialDatasetId, onDatasetSelected, registryKey = "default", onRegistryGroupSelected }: Props) {
  const selectedId = initialDatasetId;
  const [localOperatorHost, setLocalOperatorHost] = useState(false);
  useEffect(() => { setLocalOperatorHost(isLocalOperatorHost()); }, []);
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [features, setFeatures] = useState<string[]>([]);
  const [featureEditorOpen, setFeatureEditorOpen] = useState(true);
  const [nEstimators, setNEstimators] = useState(100);
  const [experimentTitle, setExperimentTitle] = useState("기본 실험");
  const [modelGroups, setModelGroups] = useState<Array<{
    registry_key: string; title: string; archived: boolean;
  }>>([]);
  const [maxSamples, setMaxSamples] = useState("auto");
  const [inputMode, setInputMode] = useState<"all" | "sample">("all");
  const [inputRows, setInputRows] = useState(100000);
  const [inputSeed, setInputSeed] = useState(42);
  const [missingPolicy, setMissingPolicy] = useState<"reject" | "train_median">("reject");
  const [loadingColumns, setLoadingColumns] = useState(false);
  const [selectedJob, setSelectedJob] = useState<MLTrainingJob | null>(null);
  const [model, setModel] = useState<ModelRevision | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [previousJobNotice, setPreviousJobNotice] = useState("");
  const currentDataset = useRef(selectedId);
  currentDataset.current = selectedId;
  const selectionRequest = useRef(0);
  const numericColumns = useMemo(
    () => columns.filter(column => NUMERIC.test(column.dtype)), [columns],
  );

  useEffect(() => {
    let mounted = true;
    void request<{items: Array<{registry_key: string; title: string; archived: boolean}>}>(
      "/api/ml/registry/groups",
    ).then(result => { if (mounted) setModelGroups(result.items.filter(g => !g.archived)); })
      .catch(() => { /* MLflow 장애는 독립 학습의 실행 제출을 직접 차단하지 않는다. */ });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    selectionRequest.current += 1;
    setColumns([]);
    setFeatures([]);
    setFeatureEditorOpen(true);
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

  function changeDataset(id: string) {
    if (id === selectedId) return;
    setPreviousJobNotice(selectedJob && !TERMINAL.has(selectedJob.status)
      ? "이전 Dataset의 학습 Job은 계속 실행됩니다. 기존 Job 상태는 ML 작업 이력에서 확인하세요."
      : "");
    setSelectedJob(null);
    setModel(null);
    setError("");
    setNotice("");
    onDatasetSelected(id);
  }

  async function startTraining() {
    if (!canTrain || !selectedId || features.length < 1 || !experimentTitle.trim() ||
        experimentTitle.trim().length > 80 || submitting ||
        (inputMode === "sample" && (!Number.isSafeInteger(inputRows) || inputRows < 2 ||
          !Number.isInteger(inputSeed) || inputSeed < 0 || inputSeed > 4294967295))) return;
    const expectedDataset = selectedId;
    const revision = ++selectionRequest.current;
    setSubmitting(true); setError(""); setNotice(""); setPreviousJobNotice("");
    try {
      const result = await submitJob<MLTrainingJob>("/api/ml/train/jobs", {
        dataset_id: selectedId, features, experiment_title: experimentTitle.trim(),
        missing_policy: missingPolicy,
        registry_key: registryKey,
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
        `/api/ml/train/jobs/${encodeURIComponent(selectedJob.id)}/fork`,
        { params: null, registry_key: registryKey });
      if (currentDataset.current === expectedDataset &&
          selectionRequest.current === revision) {
        setSelectedJob(result);
        setModel(null);
      }
    } catch (exc) { setError(formatError(exc)); }
    finally { setSubmitting(false); }
  }

  return <div className="job-list">
    {notice && (!selectedJob || !TERMINAL.has(selectedJob.status)) && <section className="alert success" role="status">
      {notice}<button type="button" onClick={() => setNotice("")} aria-label="ML 알림 닫기">×</button>
    </section>}
    {previousJobNotice && <section className="panel" role="status">
      <p>{previousJobNotice}</p>
      <button type="button" className="secondary"
        onClick={() => setPreviousJobNotice("")}>확인</button>
    </section>}
    {error && <section className="panel" role="alert">
      <strong>ML 작업을 처리하지 못했습니다.</strong> {error}
      <button className="ghost" onClick={() => setError("")}>닫기</button>
    </section>}
    <FeastFeaturePanel datasets={datasets} canCreate={canTrain} onDatasetReady={changeDataset} />
    <section className="panel">
      <div className="panel-head"><div>
        <h2>Isolation Forest 실험</h2>
        <p>Feature와 입력 정책을 직접 선택하는 독립 실험입니다. Dataset 판본과 모델 결과는 변경할 수 없습니다.</p>
      </div></div>
      <div className="form-row">
        <label>학습 Dataset
          <select aria-label="ML 학습 Dataset" value={selectedId}
            onChange={event => changeDataset(event.target.value)}>
            <option value="">선택하세요</option>
            {selectedId && !datasets.some(item => item.id === selectedId) &&
              <option value={selectedId}>현재 목록 외 Dataset · {selectedId}</option>}
            {datasets.map(dataset => <option value={dataset.id} key={dataset.id}>{dataset.name}</option>)}
          </select>
        </label>
        <label>연구 Experiment
          <input aria-label="연구 Experiment 이름" maxLength={80}
            value={experimentTitle} onChange={event => setExperimentTitle(event.target.value)}
            placeholder="연구 목적을 입력하세요" />
        </label>
        <label>등록할 모델 그룹
          <select aria-label="학습 결과 모델 그룹" value={registryKey}
            onChange={event => onRegistryGroupSelected(event.target.value)}>
            {!modelGroups.some(g => g.registry_key === registryKey) &&
              <option value={registryKey}>{registryKey === "default" ? "기본 활동 이상 탐지" : registryKey}</option>}
            {modelGroups.map(g =>
              <option key={g.registry_key} value={g.registry_key}>{g.title}</option>)}
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
        <div className="form-row">
          <label>결측값 처리 정책
            <select aria-label="학습 결측값 처리 정책" value={missingPolicy}
              onChange={event => setMissingPolicy(event.target.value as "reject" | "train_median")}>
              <option value="reject">결측 허용 안 함 (기본값)</option>
              <option value="train_median">Train 중앙값으로 대체</option>
            </select>
          </label>
        </div>
        <p className="hint">{missingPolicy === "reject"
          ? "선택한 Feature에 결측값이 있으면 학습이 실패합니다. 숫자형이라는 이유만으로 결측이 없는 것은 아닙니다."
          : "학습 데이터에서 계산한 중앙값으로 결측값을 대체합니다. 비유한값 등 다른 잘못된 입력은 그대로 검증합니다."}</p>
        <strong>학습에 사용할 숫자형 Feature ({features.length}개)</strong>
        {!selectedId && <p className="hint">Dataset을 선택하면 입력 스키마를 조회합니다.</p>}
        {loadingColumns && <p role="status" className="hint">스키마를 조회하고 있습니다.</p>}
        {selectedId && !loadingColumns && numericColumns.length === 0 &&
          <p className="hint">사용 가능한 숫자형 Feature가 없습니다. 데이터 준비에서 숫자형 Feature를 생성하세요.</p>}
        {numericColumns.length > 0 && <>
          {features.length === 0 && <p className="training-feature-guidance" role="status"
            id="training-feature-guidance">
            학습하려면 Feature를 1개 이상 선택하세요. 아래 목록을 열어 컬럼을 선택할 수 있습니다.
            숫자형이라는 이유만으로 모든 컬럼이 학습에 적합한 것은 아닙니다.
          </p>}
          <details className="training-feature-editor" open={featureEditorOpen}
            onToggle={event => setFeatureEditorOpen(event.currentTarget.open)}>
          <summary>학습 Feature 선택 · {features.length}개 선택 / {numericColumns.length}개 숫자형 컬럼</summary>
          <FeatureSelector key={selectedId} columns={numericColumns} selected={features}
            disabled={!canTrain} onChange={setFeatures} />
          </details>
        </>}
      </div>
      <details className="training-algorithm-settings"><summary>알고리즘 설정 · Isolation Forest</summary>
        <div className="form-row">
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
        <p className="hint">트리별 표본 수는 위에서 선택한 학습 입력 범위 안에서 각 트리를 구성할 때 사용하는 행 수입니다.</p>
      </details>
      <div className="actions">
        {selectedId && !loadingColumns && numericColumns.length > 0 && features.length === 0 &&
          <p className="hint" role="status">학습 Job을 제출하려면 위에서 Feature를 선택해야 합니다.</p>}
        <button disabled={!canTrain || submitting || !selectedId || features.length === 0 ||
          !experimentTitle.trim() || experimentTitle.trim().length > 80 ||
          !Number.isInteger(nEstimators) || nEstimators < 1 || nEstimators > 2000 ||
          (inputMode === "sample" && (!Number.isSafeInteger(inputRows) || inputRows < 2 ||
            !Number.isInteger(inputSeed) || inputSeed < 0 || inputSeed > 4294967295))}
          onClick={() => void startTraining()}>
          {submitting ? "Job 접수 중…" : "학습 Job 제출"}
        </button>
      </div>
    </section>
    {selectedJob && <section className="panel" aria-label="ML 학습 Job 상태">
      <div className="panel-head"><div><h2>학습 Job 상태</h2>
        <p>Job {selectedJob.id} · {jobStatusLabel(selectedJob.status)}</p>
      </div></div>
      {selectedJob.cancel_requested && selectedJob.status === "Running" &&
        <p className="hint">취소 요청 중입니다. 실제 종료 확인까지 기다립니다.</p>}
      {selectedJob.status === "Failed" && <div role="alert" className="pipeline-readiness-blocked">
        <strong>학습에 실패했습니다.</strong>
        <p>{selectedJob.error ?? "학습 실패"} · 오류 코드 {selectedJob.error_code ?? "미확인"}</p>
        {selectedJob.error_code === "SOS-ML-INPUT-INVALID" && <p>
          선택 Feature의 결측·비유한값·자료형과 입력 정책을 확인하세요.
          결측 거부로 제출한 Job이라면 Train 중앙값 대체 정책을 선택해 새로 학습할 수 있습니다.
          정확한 오류 항목은 이 코드만으로 확정할 수 없습니다.
        </p>}
        {selectedJob.error_code === "SOS-ML-TRAIN-CAPACITY" && <p>
          입력 범위에서 명시적 부분집합을 선택하거나 실행 자원을 확인하세요.
        </p>}
      </div>}
      {canCancel && ["Queued", "Running"].includes(selectedJob.status) &&
        !selectedJob.cancel_requested && <button type="button" className="secondary"
          onClick={() => void cancelJob()}>취소 요청</button>}
      {TERMINAL.has(selectedJob.status) &&
        <MLTrackingStatus key={selectedJob.id} jobId={selectedJob.id}
          operatorLinks={canInspectOperations && localOperatorHost} />}
      {selectedJob.status === "Succeeded" && <>
        <button type="button" className="secondary" disabled={!canTrain || submitting}
          onClick={() => void forkTraining()}>동일 조건 Fork·Reproduce</button>
      </>}
      {model && <div className="result">
        <h3>모델 실행 근거 · {model.id}</h3>
        <p className="hint">Dataset 판본 {model.dataset_version} · 입력 Feature {model.features.join(", ")}</p>
        <p>학습 대상 {statistic(model.training_score_summary.score_count)}행 · 이상 점수 평균
          {" "}{statistic(model.training_score_summary.score_mean)} (확률·검증 성능 아님)</p>
        <button type="button" onClick={() => onOpenModel(model.id)}>불변 모델 근거 보기 →</button>
      </div>}
    </section>}
  </div>;
}
