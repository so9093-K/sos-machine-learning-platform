import { useEffect, useMemo, useState } from "react";
import { formatError, request } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import { MLScoringPanel } from "./MLScoringPanel";
import { ScoringObservationExplorer } from "./ScoringObservationExplorer";
import { ShapContributionChart } from "./PipelineShapExplanation";
import { SensitivityChart } from "./PipelineCandidateInvestigation";
import { MLDecisionPanel, type DecisionView } from "./MLDecisionPanel";
import type { MLResourceKind } from "./PlatformNavigation";
import type {
  Dataset, MLScoringJobSummary, MLPolicyJobSummary,
  MLEvaluationJobSummary, MLExplanationJobSummary, ModelRevision, ScoringResult,
  DecisionPolicyRevision, EvaluationRevision, ExplanationResult, MLflowModelVersionPage,
} from "./types";
import {
  algorithmLabel, explanationMethodLabel, formatRecordedAt, jobStatusLabel, policyModeLabel,
  scoreDirectionLabel,
} from "./display-format";
import { EvaluationSummary } from "./EvaluationSummary";

type OpenResource = (kind: MLResourceKind, id: string) => void;
type ResultKind = "policy" | "evaluation" | "explanation";
const resultConfig = {
  policy: { title: "판정 정책", api: "/api/ml/policies", jobs: "/api/ml/policies/jobs", field: "policy_revision_id" },
  evaluation: { title: "평가", api: "/api/ml/evaluations", jobs: "/api/ml/evaluations/jobs", field: "evaluation_revision_id" },
  explanation: { title: "설명", api: "/api/ml/explanations", jobs: "/api/ml/explanations/jobs", field: "explanation_result_id" },
} as const;

function amount(n: unknown): string {
  return typeof n === "number" && Number.isFinite(n)
    ? n.toLocaleString("ko-KR", { maximumFractionDigits: 5 }) : "미정의";
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="ml-resource-field"><dt>{label}</dt><dd>{children}</dd></div>;
}
function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 8) + "…" : id;
}
/** 판본 ID 대신 읽을 수 있는 이름을 보이고, 전체 ID는 링크 title과 기술 근거에 남긴다. */
function ResourceLink({ label, id, onOpen }: { label: string; id: string; onOpen: () => void }) {
  return <button className="secondary resource-link" title={id} onClick={onOpen}>{label} ↗</button>;
}
/** 결과가 가리키는 Dataset 이름을 조회한다. 조회할 수 없으면 짧은 ID로 표시한다. */
function useDatasetName(id: string | null | undefined): string {
  const [name, setName] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => {
    if (!id) return;
    let active = true;
    void request<Dataset>(`/api/datasets/${encodeURIComponent(id)}`)
      .then(value => { if (active) setName({ id, name: value.name }); })
      .catch(() => { if (active) setName({ id, name: shortId(id) }); });
    return () => { active = false; };
  }, [id]);
  if (!id) return "";
  return name?.id === id ? name.name : shortId(id);
}

/** 설명 결과의 외부 출처와 실제 계산 입력 출처가 정확히 하나로 일치해야 한다. */
export function explanationSourceMatches(result: ExplanationResult): boolean {
  const scoringId = result.scoring_result_id ?? null;
  const playgroundId = result.playground_job_id ?? null;
  const row = result.row_index ?? null;
  if ((scoringId === null) === (playgroundId === null)) return false;
  if (scoringId !== null) {
    return row !== null && result.output.scoring_result_id === scoringId &&
      result.output.row_index === row && result.output.playground_job_id == null;
  }
  return row === null && result.output.playground_job_id === playgroundId &&
    result.output.scoring_result_id == null && result.output.row_index == null;
}

/** 설명 방법별 출력 계약에 따라 동일한 결과 판본을 표시한다. */
export function ExplanationEvidence({ result }: { result: ExplanationResult }) {
  const supported = result.method === "permutation_shap_median_v1" ||
    result.method === "median_reference_feature_replacement";
  if (!supported) return <p role="alert">
    이 설명 방법의 결과 표시는 아직 지원하지 않습니다. 계산 결과의 판본과 방법을 확인하세요.
  </p>;
  if (!result.output || typeof result.output !== "object" ||
      result.method !== result.output.method) return <p role="alert">
    설명 결과의 방법과 계산 근거가 일치하지 않습니다. 내용을 표시하지 않습니다.
  </p>;
  // 불변 Explanation 결과의 외부 식별자와 계산 근거 안의 식별자가 일치해야 한다.
  if (!explanationSourceMatches(result) ||
      result.output.model_revision_id !== result.model_revision_id) return <p role="alert">
    설명 결과의 Scoring·모델·관측 행 판본이 계산 근거와 일치하지 않습니다.
    다른 분석의 설명으로 표시하지 않습니다.
  </p>;
  if (result.method === "median_reference_feature_replacement") {
    const entries = result.output.feature_sensitivities;
    const valid = Array.isArray(entries) && entries.length > 0 && entries.every(item =>
      item && typeof item === "object" &&
      typeof item.feature === "string" && item.feature.length > 0 &&
      typeof item.anomaly_score_delta === "number" && Number.isFinite(item.anomaly_score_delta) &&
      typeof item.score_if_replaced === "number" && Number.isFinite(item.score_if_replaced) &&
      typeof item.reference_median === "number" && Number.isFinite(item.reference_median) &&
      (item.actual_value === null ||
        (typeof item.actual_value === "number" && Number.isFinite(item.actual_value))));
    if (!valid) return <p role="alert">
      치환 민감도의 Feature별 계산 근거가 없거나 유효하지 않습니다. 결과를 표시하지 않습니다.
    </p>;
  }
  return result.method === "permutation_shap_median_v1"
    ? <ShapContributionChart result={result} /> : <SensitivityChart result={result} />;
}

/** 불변 ID로 직접 조회하므로 목록 첫 페이지나 이전 학습 화면을 요구하지 않는다. */
export function MLResourceDetail({ kind, id, onOpenResource, onScoring, onDecision, canRun, canCancel }: {
  kind: MLResourceKind; id: string; onOpenResource: OpenResource;
  onScoring: (modelId: string) => void;
  onDecision: (kind: ResultKind, scoringId: string) => void;
  canRun: boolean; canCancel: boolean;
}) {
  const endpoints: Record<MLResourceKind, string> = {
    model: "/api/ml/models", scoring: "/api/ml/scoring/results",
    policy: "/api/ml/policies", evaluation: "/api/ml/evaluations",
    explanation: "/api/ml/explanations",
  };
  const key = kind + ":" + id;
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{
    key: string; data: ModelRevision | ScoringResult | DecisionPolicyRevision |
      EvaluationRevision | ExplanationResult | null; error: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    setState({ key, data: null, error: "" });
    void request<ModelRevision | ScoringResult | DecisionPolicyRevision |
      EvaluationRevision | ExplanationResult>(`${endpoints[kind]}/${encodeURIComponent(id)}`)
      .then(data => { if (active) setState({ key, data, error: "" }); })
      .catch(error => { if (active) setState({ key, data: null, error: formatError(error) }); });
    return () => { active = false; };
  }, [key, retry]);
  const current = state?.key === key ? state : null;
  const loaded = current?.data as Partial<ModelRevision> | null | undefined;
  const datasetName = useDatasetName(kind === "model" || kind === "scoring" ? loaded?.dataset_id : null);
  if (current?.error) return <section className="panel" role="alert">
    <h2>결과 열기 실패</h2><p>{current.error}</p>
    <p className="hint">판본 ID, 접근 권한 또는 서비스 상태를 확인하세요.</p>
    <button className="secondary" onClick={() => setRetry(value => value + 1)}>다시 조회</button>
  </section>;
  if (!current?.data) return <section className="panel" role="status">불변 결과를 조회하는 중입니다.</section>;
  const data = current.data;
  return <section className="panel ml-resource-detail">
<div className="panel-head"><div><h2>{{
      model: "모델 판본", scoring: "모델 적용 분석 결과", policy: "판정 정책",
      evaluation: "평가 결과", explanation: "설명 결과",
    }[kind]}</h2><p>생성 {formatRecordedAt(data.created_at)}</p></div></div>
    {kind === "model" && (() => {
      const m = data as ModelRevision;
      return <>
        <div className="ml-resource-summary">
          <h3>{algorithmLabel(m.algorithm)}{m.registry_version ? ` · v${m.registry_version}` : ""} · 학습 {formatRecordedAt(m.created_at)}</h3>
          <p>{m.lifecycle_state === "Active"
            ? "학습에서 생성한 불변 결과입니다. 새 학습 없이 기존 모델을 활용할 수 있습니다."
            : m.lifecycle_state === "Deleting"
              ? "모델 파일을 삭제 중입니다. 현재 새 점수화에 사용할 수 없습니다."
              : "모델 파일과 Registry 버전이 삭제됐습니다. 과거 실행 근거만 남아 있습니다."}</p>
          <div className="ml-resource-actions">
            <button disabled={m.lifecycle_state !== "Active"}
              onClick={() => onScoring(m.id)}>이 모델로 점수 계산 →</button>
          </div>
        </div>
        <dl className="ml-resource-fields">
          <Field label="학습 Dataset">{datasetName}</Field>
          <Field label="입력 Feature 수">{m.features.length.toLocaleString("ko-KR")}개</Field>
        </dl>
        <p className="hint">학습 데이터의 점수 요약은 독립 검증 성능을 의미하지 않습니다.</p>
        <details className="ml-resource-evidence">
          <summary>모델 입력·학습 Job·Runtime 근거</summary>
          <dl className="ml-resource-fields">
            <Field label="전체 Feature 및 순서">{m.features.join(", ")}</Field>
            <Field label="모델 판본 ID">{m.id}</Field>
            <Field label="Dataset ID · 판본">{m.dataset_id} · {m.dataset_version}</Field>
            <Field label="학습 Job">{m.job_id}</Field>
            <Field label="Runtime">{String(m.runtime.scikit_learn ?? "상세 근거 미확인")}</Field>
          </dl>
        </details>
      </>;
    })()}
    {kind === "scoring" && (() => {
      const r = data as ScoringResult;
      return <>
        <dl className="ml-resource-fields"><Field label="분석 Dataset">{datasetName}</Field></dl>
        <ScoringObservationExplorer key={r.id} result={r} canRun={canRun}
          canCancel={canCancel} onOpenResource={onOpenResource} />
        <div className="ml-resource-actions">
          {(["policy", "evaluation", "explanation"] as const).map(k =>
            <button key={k} className="secondary"
              onClick={() => onDecision(k, r.id)}>
              {resultConfig[k].title}
            </button>)}
        </div>
        <details className="ml-resource-evidence">
          <summary>모델 판본 · 입력 Feature · 평가 지표와 기술 근거</summary>
          <dl className="ml-resource-fields">
            <Field label="모델 판본"><ResourceLink label="모델 판본 열기" id={r.model_revision_id}
              onOpen={() => onOpenResource("model", r.model_revision_id)} /></Field>
            <Field label="Dataset ID · 판본">{r.dataset_id} · {r.dataset_version}</Field>
            <Field label="Feature 서명">{r.feature_names.join(", ")}</Field>
            <Field label="점수 방향">{scoreDirectionLabel(r.score_direction)}</Field>
            <Field label="정답 레이블">{r.label_column ?? "없음"}</Field>
            {typeof r.ranking_metrics?.average_precision === "number" &&
              <Field label="Average Precision">{amount(r.ranking_metrics.average_precision)}</Field>}
            {typeof r.ranking_metrics?.roc_auc === "number" &&
              <Field label="ROC-AUC">{amount(r.ranking_metrics.roc_auc)}</Field>}
          </dl>
        </details>
      </>;
    })()}
    {kind === "policy" && (() => {
      const r = data as DecisionPolicyRevision;
      return <>
        <dl className="ml-resource-fields">
          <Field label="정책 방식">{policyModeLabel(r.mode)}</Field>
          <Field label="임계값">{amount(r.threshold)}</Field>
          <Field label="설정 경보 비율">{r.alert_fraction === null ? "해당 없음" : amount(r.alert_fraction)}</Field>
          <Field label="실제 이상 판정 건수">{amount(r.decision_summary.predicted_anomalies)}</Field>
          <Field label="기준 점수 결과"><ResourceLink label="점수 결과 열기" id={r.scoring_result_id}
            onOpen={() => onOpenResource("scoring", r.scoring_result_id)} /></Field>
        </dl>
        <p className="hint">동점이 있을 때 설정된 경보 비율과 실제 판정 비율은 다를 수 있습니다.</p>
      </>;
    })()}
    {kind === "evaluation" && (() => {
      const r = data as EvaluationRevision;
      return <>
        <EvaluationSummary evaluation={r} />
        <div className="ml-resource-actions">
          <ResourceLink label="평가한 점수 결과" id={r.scoring_result_id}
            onOpen={() => onOpenResource("scoring", r.scoring_result_id)} />
          {r.policy_revision_id && <ResourceLink label="적용한 판정 정책" id={r.policy_revision_id}
            onOpen={() => onOpenResource("policy", r.policy_revision_id!)} />}
        </div>
        <p className="hint">레이블·분모가 없거나 불충분한 지표는 미정의입니다. 동일 점수로 정한 임계값의 평가를 독립 검증 성능으로 해석하지 마세요.</p>
      </>;
    })()}
    {kind === "explanation" && (() => {
      const r = data as ExplanationResult;
      return <>
        <dl className="ml-resource-fields">
          <Field label="방법">{explanationMethodLabel(r.method)}</Field>
          {r.scoring_result_id ? <Field label="출처"><ResourceLink label="점수 결과 열기"
            id={r.scoring_result_id} onOpen={() => onOpenResource("scoring", r.scoring_result_id!)} /></Field>
            : <Field label="출처">단일 입력 점수 계산 · {shortId(r.playground_job_id ?? "출처 없음")}</Field>}
          <Field label="모델 판본"><ResourceLink label="모델 판본 열기" id={r.model_revision_id}
            onOpen={() => onOpenResource("model", r.model_revision_id)} /></Field>
          {r.row_index !== null && r.row_index !== undefined &&
            <Field label="결과 내부 행 번호">{r.row_index}</Field>}
        </dl>
        <ExplanationEvidence result={r} />
        {r.scoring_result_id && <p className="hint">결과 행 번호는 Scoring Result 내부 순서이지 원본 관측치 ID가 아닙니다.</p>}
      </>;
    })()}
  </section>;
}

function revisionId(job: { result: { [key: string]: unknown } | null }, key: string): string | null {
  const value = job.result?.[key];
  return typeof value === "string" ? value : null;
}

type JobRow = { status: string; created_at: string; error?: string | null; error_code?: string | null };
/** 작업 이력 한 줄: 대상·상태·시각을 보이고 실패하면 원인 메시지를 함께 보인다. */
function JobRowSummary({ job, subject }: { job: JobRow; subject: string }) {
  return <span className="ml-resource-row-summary">
    <strong>{subject}</strong>
    <small>{jobStatusLabel(job.status)} · {formatRecordedAt(job.created_at)}</small>
    {job.status === "Failed" && <small className="ml-resource-row-error" title={job.error_code ?? undefined}>
      {job.error ?? job.error_code ?? "작업 실패"}</small>}
  </span>;
}

/** 특정 학습 화면을 거치지 않고 Model Revision과 Dataset으로 Scoring한다. */
export function ScoringWorkspace({ datasets, preferredModelId, canRun, canCancel,
  onOpenResource, onModelSelected, onNavigate }: {
  datasets: Dataset[]; preferredModelId: string; canRun: boolean; canCancel: boolean;
  onOpenResource: OpenResource; onModelSelected: (id: string) => void;
  onNavigate: (tab: "ml" | "scoring") => void;
}) {
  const [registeredModels, setRegisteredModels] = useState<MLflowModelVersionPage["items"]>([]);
  const [nextModelPage, setNextModelPage] = useState<string | null>(null);
  const scores = usePagedJobs<MLScoringJobSummary>("/api/ml/scoring/jobs");
  const [modelId, setModelId] = useState(preferredModelId);
  const [loadedModelId, setLoadedModelId] = useState(preferredModelId);
  const [modelLookupRevision, retryModelLookup] = useState(0);
  const [model, setModel] = useState<ModelRevision | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setModelId(preferredModelId);
    setLoadedModelId(preferredModelId);
  }, [preferredModelId]);
  useEffect(() => {
    let active = true;
    void request<MLflowModelVersionPage>("/api/ml/registry/models")
      .then(page => {
        if (active) {
          setRegisteredModels(page.items);
          setNextModelPage(page.next_page_token ?? null);
        }
      })
      .catch(e => { if (active) setError(formatError(e)); });
    void scores.refresh().catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    setModel(null);
    setError("");
    if (loadedModelId) {
      void request<ModelRevision>(`/api/ml/models/${encodeURIComponent(loadedModelId)}`)
        .then(value => { if (active) { setModel(value); setError(""); } })
        .catch(e => { if (active) setError(formatError(e)); });
    }
    return () => { active = false; };
  }, [loadedModelId, modelLookupRevision]);
  const available = useMemo(() => [...new Set(registeredModels.map(item => item.model_id))], [registeredModels]);
  return <div className="ml-resource-workspace">
    <section className="panel">
      <h2>점수 계산 모델</h2>
      <p className="hint">기존 모델 버전을 선택하거나 ID를 직접 입력할 수 있습니다. 새 학습은 필요하지 않습니다.
        목록은 MLflow Registry에서 현재 Actor가 이용할 수 있는 모델을 조회합니다.</p>
      {error && <p role="alert">{error}</p>}
      <div className="form-row">
        <label>모델 판본 ID
          <input value={modelId} onChange={event => setModelId(event.target.value)}
            placeholder="기존 모델 버전 ID" aria-label="점수화 대상 모델 버전 ID"/>
        </label>
        <label>MLflow 등록 모델 버전
          <select value={available.includes(modelId) ? modelId : ""}
            aria-label="조회된 모델 버전 선택" onChange={event => {
              setModelId(event.target.value);
              setLoadedModelId(event.target.value);
              onModelSelected(event.target.value);
            }}>
            <option value="">모델 선택</option>
            {registeredModels.map(item => <option key={item.name + item.version} value={item.model_id}>
              {item.name} · v{item.version}
            </option>)}
          </select>
        </label>
      </div>
      {nextModelPage && <button type="button" className="secondary" onClick={() => {
        const token = nextModelPage;
        setNextModelPage(null);
        void request<MLflowModelVersionPage>(
          `/api/ml/registry/models?page_token=${encodeURIComponent(token)}`,
        ).then(page => {
          setRegisteredModels(old => [...old, ...page.items]);
          setNextModelPage(page.next_page_token ?? null);
        }).catch(e => { setNextModelPage(token); setError(formatError(e)); });
      }}>모델 버전 더 보기</button>}
      <button type="button" className="secondary" disabled={!modelId.trim()}
        onClick={() => {
          const identifier = modelId.trim();
          if (identifier === loadedModelId) retryModelLookup(n => n + 1);
          else setLoadedModelId(identifier);
          onModelSelected(identifier);
        }}>모델 버전 조회</button>
      {modelId.trim() !== loadedModelId && <p className="hint">
        모델 ID가 변경됐습니다. 조회 버튼을 눌러 선택을 확정하세요.</p>}
      {model && model.id === loadedModelId && preferredModelId === loadedModelId &&
        modelId.trim() === loadedModelId &&
        <p className="hint">선택 모델: {model.registry_version ? `v${model.registry_version} · ` : ""}{algorithmLabel(model.algorithm)} · 학습 {formatRecordedAt(model.created_at)}
          · 입력 Feature {model.features.length}개</p>}
      {!loadedModelId && <div className="steps">
        <p className="hint">점수를 계산하려면 기존 모델 판본을 선택하세요. 모델이 없다면 독립 학습을 시작할 수 있습니다.</p>
        <button className="secondary" type="button" onClick={() => onNavigate("ml")}>
          모델 버전·학습 확인 →
        </button>
      </div>}
    </section>
    {model && model.id === loadedModelId && preferredModelId === loadedModelId &&
      modelId.trim() === loadedModelId &&
      <MLScoringPanel key={model.id} model={model} datasets={datasets}
      canRun={canRun} canCancel={canCancel} onOpenResult={id => onOpenResource("scoring", id)}
      onOpenResource={onOpenResource} />}
    <section className="panel">
      <div className="panel-head"><div><h2>전체 점수 계산 이력</h2>
        <p>이 화면에서 실행하지 않은 기존 점수 계산도 찾을 수 있습니다. 현재 조회된 목록 기준입니다.</p></div>
        <button className="secondary" onClick={() => void scores.refresh().catch(e => setError(formatError(e)))}>새로고침</button>
      </div>
      <div className="ml-resource-list">
        {scores.items.map(j => <div key={j.id} className="ml-resource-row">
          <JobRowSummary job={j} subject={datasets.find(d => d.id === j.dataset_id)?.name ??
            (j.dataset_id ? shortId(j.dataset_id) : "Dataset 미상")} />
          {revisionId(j,"scoring_result_id") &&
            <button className="secondary"
              onClick={() => onOpenResource("scoring", revisionId(j,"scoring_result_id")!)}>
              점수 결과 열기 →
            </button>}
        </div>)}
        {scores.items.length === 0 && <p className="muted">조회된 점수 계산이 없습니다.</p>}
      </div>
      {scores.nextCursor && <button className="secondary"
        onClick={() => void scores.loadMore().catch(e => setError(formatError(e)))}>이전 작업 더 보기</button>}
    </section>
  </div>;
}

/** Scoring Result를 직접 선택해 정책·평가·설명을 독립 실행한다. */
export function DecisionWorkspace({ view, preferredScoringId, canRun, canCancel,
  onOpenResource, onScoringSelected, onNavigate, datasets = [] }: {
  view: DecisionView; preferredScoringId: string; datasets?: Dataset[];
  canRun: boolean; canCancel: boolean; onOpenResource: OpenResource;
  onScoringSelected: (id: string) => void;
  onNavigate: (tab: "scoring") => void;
}) {
  const scores = usePagedJobs<MLScoringJobSummary>("/api/ml/scoring/jobs");
  const policies = usePagedJobs<MLPolicyJobSummary>("/api/ml/policies/jobs");
  const evaluations = usePagedJobs<MLEvaluationJobSummary>("/api/ml/evaluations/jobs");
  const explanations = usePagedJobs<MLExplanationJobSummary>("/api/ml/explanations/jobs");
  const histories = { policy: policies, evaluation: evaluations, explanation: explanations };
  const history = histories[view];
  const [scoringId, setScoringId] = useState(preferredScoringId);
  const [loadedScoringId, setLoadedScoringId] = useState(preferredScoringId);
  const [scoringLookupRevision, retryScoringLookup] = useState(0);
  const [scoring, setScoring] = useState<ScoringResult | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setScoringId(preferredScoringId);
    setLoadedScoringId(preferredScoringId);
  }, [preferredScoringId]);
  useEffect(() => {
    void scores.refresh().catch(e => setError(formatError(e)));
    void history.refresh().catch(e => setError(formatError(e)));
  }, [view]);
  useEffect(() => {
    let active = true; setScoring(null); setError("");
    if (loadedScoringId) void request<ScoringResult>(
      `/api/ml/scoring/results/${encodeURIComponent(loadedScoringId)}`)
      .then(value => { if (active) { setScoring(value); setError(""); } })
      .catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, [loadedScoringId, scoringLookupRevision]);
  // 점수 결과 ID 대신 분석한 Dataset과 계산 시각으로 고른다.
  const available = useMemo(() => {
    const options = new Map<string, string>();
    for (const job of scores.items) {
      const id = revisionId(job, "scoring_result_id");
      if (!id || options.has(id)) continue;
      const dataset = datasets.find(d => d.id === job.dataset_id)?.name ??
        (job.dataset_id ? shortId(job.dataset_id) : "Dataset 미상");
      options.set(id, `${dataset} · ${formatRecordedAt(job.created_at)}`);
    }
    return options;
  }, [scores.items, datasets]);
  const config = resultConfig[view];
  return <div className="ml-resource-workspace">
    <section className="panel">
      <h2>{config.title} · 원본 점수 선택</h2>
      <p className="hint">이미 계산된 점수 결과를 재사용합니다. 학습이나 재점수화 없이 실행할 수 있습니다.</p>
      {error && <p role="alert">{error}</p>}
      <div className="form-row">
        <label>점수 결과 ID
          <input value={scoringId} aria-label="선택할 점수 결과 ID"
            onChange={event => setScoringId(event.target.value)} placeholder="기존 결과 ID"/>
        </label>
        <label>조회된 점수 결과
          <select aria-label="조회된 점수 결과 선택" value={available.has(scoringId) ? scoringId : ""}
            onChange={event => {
              setScoringId(event.target.value);
              setLoadedScoringId(event.target.value);
              onScoringSelected(event.target.value);
            }}>
            <option value="">결과 선택</option>
            {[...available].map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
      </div>
      {scores.nextCursor && <button className="secondary"
        onClick={() => void scores.loadMore().catch(e => setError(formatError(e)))}>이전 점수 결과 더 보기</button>}
      <button type="button" className="secondary" disabled={!scoringId.trim()}
        onClick={() => {
          const identifier = scoringId.trim();
          if (identifier === loadedScoringId) retryScoringLookup(n => n + 1);
          else setLoadedScoringId(identifier);
          onScoringSelected(identifier);
        }}>점수 결과 조회</button>
      {scoringId.trim() !== loadedScoringId && <p className="hint">
        점수 결과 ID가 변경됐습니다. 조회 버튼을 눌러 선택을 확정하세요.</p>}
      {scoring && scoring.id === loadedScoringId && preferredScoringId === loadedScoringId &&
        scoringId.trim() === loadedScoringId &&
        <p>점수화된 행 {scoring.row_count.toLocaleString("ko-KR")}건 · 생성 {formatRecordedAt(scoring.created_at)}</p>}
      {!loadedScoringId && <div className="steps">
        <p className="hint">평가·정책·설명은 이미 계산된 점수 결과를 독립적으로 재사용합니다.
          기존 결과 ID를 선택하거나 먼저 모델 점수를 계산하세요.</p>
        <button type="button" className="secondary" onClick={() => onNavigate("scoring")}>
          모델 점수화 화면으로 →
        </button>
      </div>}
    </section>
    {scoring && scoring.id === loadedScoringId && preferredScoringId === loadedScoringId &&
      scoringId.trim() === loadedScoringId && <section className="panel">
      <MLDecisionPanel key={scoring.id + view} scoring={scoring} view={view}
        canRun={canRun} canCancel={canCancel}
        onOpenResult={(kind,id) => onOpenResource(kind,id)} />
    </section>}
    <section className="panel">
      <div className="panel-head"><div><h2>{config.title} 작업 이력</h2>
        <p>현재 조회된 Job에서 결과 판본을 찾습니다. 개별 결과 ID로 직접 다시 열 수 있습니다.</p></div>
        <button className="secondary" onClick={() => void history.refresh().catch(e => setError(formatError(e)))}>새로고침</button>
      </div>
      <div className="ml-resource-list">
        {history.items.map(j => <div className="ml-resource-row" key={j.id}>
          <JobRowSummary job={j} subject={config.title} />
          {revisionId(j,config.field) && <button className="secondary"
            onClick={() => onOpenResource(view, revisionId(j,config.field)!)}>
            결과 판본 열기 →
          </button>}
        </div>)}
        {history.items.length === 0 && <p className="muted">조회된 {config.title} Job이 없습니다.</p>}
      </div>
      {history.nextCursor && <button className="secondary"
        onClick={() => void history.loadMore().catch(e => setError(formatError(e)))}>이전 결과 더 보기</button>}
    </section>
  </div>;
}
