import { useEffect, useMemo, useState } from "react";
import { formatError, request } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import { MLScoringPanel } from "./MLScoringPanel";
import { MLDecisionPanel, type DecisionView } from "./MLDecisionPanel";
import type { MLResourceKind } from "./PlatformNavigation";
import type {
  Dataset, MLScoringJobSummary, MLPolicyJobSummary,
  MLEvaluationJobSummary, MLExplanationJobSummary, ModelRevision, ScoringResult,
  DecisionPolicyRevision, EvaluationRevision, ExplanationResult,
} from "./types";

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

/** 불변 ID로 직접 조회하므로 목록 첫 페이지나 이전 학습 화면을 요구하지 않는다. */
export function MLResourceDetail({ kind, id, onOpenResource, onScoring, onDecision }: {
  kind: MLResourceKind; id: string; onOpenResource: OpenResource;
  onScoring: (modelId: string) => void;
  onDecision: (kind: ResultKind, scoringId: string) => void;
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
  if (current?.error) return <section className="panel" role="alert">
    <h3>결과를 열 수 없습니다.</h3><p>{current.error}</p>
    <p className="hint">판본 ID, 접근 권한 또는 서비스 상태를 확인하세요.</p>
    <button className="secondary" onClick={() => setRetry(value => value + 1)}>다시 조회</button>
  </section>;
  if (!current?.data) return <section className="panel" role="status">불변 결과를 조회하는 중입니다.</section>;
  const data = current.data;
  return <section className="panel ml-resource-detail">
    <div className="panel-head"><div><h3>{{
      model: "Model Revision", scoring: "Scoring Result", policy: "Decision Policy",
      evaluation: "Evaluation", explanation: "Explanation",
    }[kind]}</h3><p>불변 ID · {id}</p></div></div>
    {kind === "model" && (() => {
      const m = data as ModelRevision;
      return <>
        <div className="ml-resource-summary">
          <h4>{m.algorithm} · 모델 판본</h4>
          <p>학습에서 생성한 불변 결과입니다. 새 학습 없이 기존 모델을 활용할 수 있습니다.</p>
          <div className="ml-resource-actions">
            <button onClick={() => onScoring(m.id)}>이 모델로 점수화 시작 →</button>
          </div>
        </div>
        <dl className="ml-resource-fields">
          <Field label="원본 Dataset">{m.dataset_id}</Field>
          <Field label="Dataset 판본">{m.dataset_version}</Field>
          <Field label="입력 Feature 수">{m.features.length.toLocaleString("ko-KR")}개</Field>
          <Field label="생성 시각">{new Date(m.created_at).toLocaleString("ko-KR")}</Field>
        </dl>
        <p className="hint">학습 데이터의 점수 요약은 독립 검증 성능을 의미하지 않습니다.</p>
        <details className="ml-resource-evidence">
          <summary>모델 입력·학습 Job·Runtime 근거 확인</summary>
          <dl className="ml-resource-fields">
            <Field label="전체 Feature 및 순서">{m.features.join(", ")}</Field>
            <Field label="Training Job">{m.job_id}</Field>
            <Field label="Runtime">{String(m.runtime.scikit_learn ?? "상세 근거 미확인")}</Field>
          </dl>
        </details>
      </>;
    })()}
    {kind === "scoring" && (() => {
      const r = data as ScoringResult;
      return <>
        <dl className="ml-resource-fields">
          <Field label="모델 판본"><button className="secondary"
            onClick={() => onOpenResource("model", r.model_revision_id)}>{r.model_revision_id} ↗</button></Field>
          <Field label="Dataset 판본">{r.dataset_id} · {r.dataset_version}</Field>
          <Field label="Feature 서명">{r.feature_names.join(", ")}</Field>
          <Field label="점수 방향">{r.score_direction}</Field>
          <Field label="점수화 범위">{r.row_count.toLocaleString("ko-KR")}행</Field>
          <Field label="정답 레이블">{r.label_column ?? "없음"}</Field>
          <Field label="Average Precision">{amount(r.ranking_metrics?.average_precision)}</Field>
          <Field label="ROC-AUC">{amount(r.ranking_metrics?.roc_auc)}</Field>
        </dl>
        <p className="hint">점수는 확률이 아니며, 레이블이 없거나 정의되지 않는 지표는 미정의로 표시합니다.
          학습 Dataset과의 관계만으로 독립 검증을 주장하지 않습니다.</p>
        <div className="ml-resource-actions">
          {(["policy", "evaluation", "explanation"] as const).map(k =>
            <button key={k} className="secondary"
              onClick={() => onDecision(k, r.id)}>
              {resultConfig[k].title}
            </button>)}
        </div>
        <p className="hint">해당 Scoring Result를 고정한 상태로 정책·평가·설명 작업을 독립 실행할 수 있습니다.</p>
      </>;
    })()}
    {kind === "policy" && (() => {
      const r = data as DecisionPolicyRevision;
      return <>
        <dl className="ml-resource-fields">
          <Field label="기준 Scoring"><button className="secondary"
            onClick={() => onOpenResource("scoring", r.scoring_result_id)}>{r.scoring_result_id} ↗</button></Field>
          <Field label="정책 방식">{r.mode}</Field>
          <Field label="임계값">{amount(r.threshold)}</Field>
          <Field label="설정 경보 비율">{r.alert_fraction === null ? "해당 없음" : amount(r.alert_fraction)}</Field>
          <Field label="실제 이상 판정 건수">{amount(r.decision_summary.predicted_anomalies)}</Field>
          <Field label="생성 시각">{r.created_at}</Field>
        </dl>
        <p className="hint">동점이 있을 때 설정된 경보 비율과 실제 판정 비율은 다를 수 있습니다.</p>
      </>;
    })()}
    {kind === "evaluation" && (() => {
      const r = data as EvaluationRevision;
      return <>
        <dl className="ml-resource-fields">
          <Field label="Scoring Result"><button className="secondary"
            onClick={() => onOpenResource("scoring", r.scoring_result_id)}>{r.scoring_result_id} ↗</button></Field>
          <Field label="적용 정책">{r.policy_revision_id
            ? <button className="secondary" onClick={() => onOpenResource("policy", r.policy_revision_id!)}>
              {r.policy_revision_id} ↗</button> : "없음 (순위 평가만)"}</Field>
          <Field label="레이블 근거">{r.label_coverage}</Field>
          <Field label="Precision">{amount(r.decision_metrics.precision)}</Field>
          <Field label="Recall">{amount(r.decision_metrics.recall)}</Field>
          <Field label="F1">{amount(r.decision_metrics.f1)}</Field>
          <Field label="ROC-AUC">{amount(r.ranking_metrics?.roc_auc)}</Field>
        </dl>
        <p className="hint">레이블·분모가 없거나 불충분한 지표는 미정의입니다. 동일 점수로 정한 임계값의 평가를 독립 검증 성능으로 해석하지 마세요.</p>
      </>;
    })()}
    {kind === "explanation" && (() => {
      const r = data as ExplanationResult;
      const values = Array.isArray(r.output.feature_sensitivities)
        ? r.output.feature_sensitivities as Array<Record<string, unknown>> : [];
      return <>
        <dl className="ml-resource-fields">
          <Field label="Scoring Result"><button className="secondary"
            onClick={() => onOpenResource("scoring", r.scoring_result_id)}>{r.scoring_result_id} ↗</button></Field>
          <Field label="모델 판본"><button className="secondary"
            onClick={() => onOpenResource("model", r.model_revision_id)}>{r.model_revision_id} ↗</button></Field>
          <Field label="방법">{r.method}</Field>
          <Field label="결과 내부 행 번호">{r.row_index}</Field>
        </dl>
        <div className="table-scroll"><table><caption>Feature 참조 중앙값 치환에 따른 점수 민감도</caption>
          <thead><tr><th>Feature</th><th>실제값</th><th>참조 중앙값</th><th>점수 변화</th></tr></thead>
          <tbody>{values.map((x,i) => <tr key={i}><th>{String(x.feature)}</th>
            <td>{amount(x.actual_value)}</td><td>{amount(x.reference_median)}</td>
            <td>{amount(x.anomaly_score_delta)}</td></tr>)}</tbody>
        </table></div>
        <p className="hint">국소 점수 민감도이며 SHAP·LIME·인과적 원인·가법 기여도로 해석하지 않습니다.
          행 번호는 Scoring Result 내부 순서이지 원본 관측치 ID가 아닙니다.</p>
      </>;
    })()}
  </section>;
}

function revisionId(job: { result: { [key: string]: unknown } | null }, key: string): string | null {
  const value = job.result?.[key];
  return typeof value === "string" ? value : null;
}

/** 특정 학습 화면을 거치지 않고 Model Revision과 Dataset으로 Scoring한다. */
export function ScoringWorkspace({ datasets, preferredModelId, canRun, canCancel,
  onOpenResource, onModelSelected, onNavigate }: {
  datasets: Dataset[]; preferredModelId: string; canRun: boolean; canCancel: boolean;
  onOpenResource: OpenResource; onModelSelected: (id: string) => void;
  onNavigate: (tab: "ml" | "scoring") => void;
}) {
  const [registeredModels, setRegisteredModels] = useState<Array<{model_id: string; name: string; version: string}>>([]);
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
    void request<{ items: Array<{model_id: string; name: string; version: string}>; next_page_token: string | null }>("/api/ml/registry/models")
      .then(page => {
        if (active) {
          setRegisteredModels(page.items);
          setNextModelPage(page.next_page_token);
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
      <h3>Scoring 대상 모델</h3>
      <p className="hint">기존 모델 판본을 선택하거나 ID를 직접 입력할 수 있습니다. 새 학습은 필요하지 않습니다.
        목록은 MLflow Registry에서 현재 Actor가 이용할 수 있는 모델을 조회합니다.</p>
      {error && <p role="alert">{error}</p>}
      <div className="form-row">
        <label>Model Revision ID
          <input value={modelId} onChange={event => setModelId(event.target.value)}
            placeholder="기존 모델 판본 ID" aria-label="점수화 대상 모델 판본 ID"/>
        </label>
        <label>MLflow 등록 모델 판본
          <select value={available.includes(modelId) ? modelId : ""}
            aria-label="조회된 모델 판본 선택" onChange={event => {
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
        void request<{ items: Array<{model_id: string; name: string; version: string}>; next_page_token: string | null }>(
          `/api/ml/registry/models?page_token=${encodeURIComponent(token)}`,
        ).then(page => {
          setRegisteredModels(old => [...old, ...page.items]);
          setNextModelPage(page.next_page_token);
        }).catch(e => { setNextModelPage(token); setError(formatError(e)); });
      }}>모델 판본 더 보기</button>}
      <button type="button" className="secondary" disabled={!modelId.trim()}
        onClick={() => {
          const identifier = modelId.trim();
          if (identifier === loadedModelId) retryModelLookup(n => n + 1);
          else setLoadedModelId(identifier);
          onModelSelected(identifier);
        }}>모델 판본 조회</button>
      {modelId.trim() !== loadedModelId && <p className="hint">
        모델 ID가 변경됐습니다. 조회 버튼을 눌러 선택을 확정하세요.</p>}
      {model && model.id === loadedModelId && preferredModelId === loadedModelId &&
        modelId.trim() === loadedModelId &&
        <p className="hint">선택 모델: {model.algorithm} · {model.id} · 입력 Feature {model.features.length}개</p>}
      {!loadedModelId && <div className="steps">
        <p className="hint">점수화하려면 기존 Model Revision을 선택하세요. 모델이 없다면 독립 학습을 시작할 수 있습니다.</p>
        <button className="secondary" type="button" onClick={() => onNavigate("ml")}>
          모델 판본·학습 확인 →
        </button>
      </div>}
    </section>
    {model && model.id === loadedModelId && preferredModelId === loadedModelId &&
      modelId.trim() === loadedModelId &&
      <MLScoringPanel key={model.id} model={model} datasets={datasets}
      canRun={canRun} canCancel={canCancel} onOpenResult={id => onOpenResource("scoring", id)} />}
    <section className="panel">
      <div className="panel-head"><div><h3>전체 Scoring Job 이력</h3>
        <p>이 화면에서 실행하지 않은 기존 Scoring도 찾을 수 있습니다. 현재 조회된 목록 기준입니다.</p></div>
        <button className="secondary" onClick={() => void scores.refresh().catch(e => setError(formatError(e)))}>새로고침</button>
      </div>
      <div className="ml-resource-list">
        {scores.items.map(j => <div key={j.id} className="ml-resource-row">
          <span>{j.id.slice(0,12)} · {j.status} · {j.dataset_id}</span>
          {revisionId(j,"scoring_result_id") &&
            <button className="secondary"
              onClick={() => onOpenResource("scoring", revisionId(j,"scoring_result_id")!)}>
              Scoring Result 열기 →
            </button>}
        </div>)}
        {scores.items.length === 0 && <p className="muted">조회된 Scoring Job이 없습니다.</p>}
      </div>
      {scores.nextCursor && <button className="secondary"
        onClick={() => void scores.loadMore().catch(e => setError(formatError(e)))}>이전 작업 더 보기</button>}
    </section>
  </div>;
}

/** Scoring Result를 직접 선택해 정책·평가·설명을 독립 실행한다. */
export function DecisionWorkspace({ view, preferredScoringId, canRun, canCancel,
  onOpenResource, onScoringSelected, onNavigate }: {
  view: DecisionView; preferredScoringId: string;
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
  const available = useMemo(() => [...new Set(scores.items.map(j =>
    revisionId(j,"scoring_result_id")).filter((id): id is string => Boolean(id)))], [scores.items]);
  const config = resultConfig[view];
  return <div className="ml-resource-workspace">
    <section className="panel">
      <h3>{config.title} · 원본 점수 선택</h3>
      <p className="hint">이미 계산된 점수 결과를 재사용합니다. 학습이나 재점수화 없이 실행할 수 있습니다.</p>
      {error && <p role="alert">{error}</p>}
      <div className="form-row">
        <label>Scoring Result ID
          <input value={scoringId} aria-label="선택할 Scoring Result ID"
            onChange={event => setScoringId(event.target.value)} placeholder="기존 결과 ID"/>
        </label>
        <label>조회된 Scoring Result
          <select aria-label="조회된 점수 결과 선택" value={available.includes(scoringId) ? scoringId : ""}
            onChange={event => {
              setScoringId(event.target.value);
              setLoadedScoringId(event.target.value);
              onScoringSelected(event.target.value);
            }}>
            <option value="">결과 선택</option>
            {available.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
      </div>
      {scores.nextCursor && <button className="secondary"
        onClick={() => void scores.loadMore().catch(e => setError(formatError(e)))}>이전 Scoring 더 보기</button>}
      <button type="button" className="secondary" disabled={!scoringId.trim()}
        onClick={() => {
          const identifier = scoringId.trim();
          if (identifier === loadedScoringId) retryScoringLookup(n => n + 1);
          else setLoadedScoringId(identifier);
          onScoringSelected(identifier);
        }}>Scoring Result 조회</button>
      {scoringId.trim() !== loadedScoringId && <p className="hint">
        Scoring Result ID가 변경됐습니다. 조회 버튼을 눌러 선택을 확정하세요.</p>}
      {scoring && scoring.id === loadedScoringId && preferredScoringId === loadedScoringId &&
        scoringId.trim() === loadedScoringId &&
        <p>점수화된 행: {scoring.row_count.toLocaleString("ko-KR")} · 모델 {scoring.model_revision_id}</p>}
      {!loadedScoringId && <div className="steps">
        <p className="hint">평가·정책·설명은 이미 계산된 Scoring Result를 독립적으로 재사용합니다.
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
      <div className="panel-head"><div><h3>{config.title} 작업 이력</h3>
        <p>현재 조회된 Job에서 결과 판본을 찾습니다. 개별 결과 ID로 직접 다시 열 수 있습니다.</p></div>
        <button className="secondary" onClick={() => void history.refresh().catch(e => setError(formatError(e)))}>새로고침</button>
      </div>
      <div className="ml-resource-list">
        {history.items.map(j => <div className="ml-resource-row" key={j.id}>
          <span>{j.id.slice(0,12)} · {j.status} · {j.created_at}</span>
          {revisionId(j,config.field) && <button className="secondary"
            onClick={() => onOpenResource(view, revisionId(j,config.field)!)}>
            결과 판본 열기 →
          </button>}
          {j.status === "Failed" && <small>{j.error_code ?? "작업 실패"}</small>}
        </div>)}
        {history.items.length === 0 && <p className="muted">조회된 {config.title} Job이 없습니다.</p>}
      </div>
      {history.nextCursor && <button className="secondary"
        onClick={() => void history.loadMore().catch(e => setError(formatError(e)))}>이전 결과 더 보기</button>}
    </section>
  </div>;
}
