import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import type {
  DecisionPolicyRevision, EvaluationRevision, ExplanationResult,
  MLEvaluationJob, MLEvaluationJobSummary, MLExplanationJob,
  MLExplanationJobSummary, MLPolicyJob, MLPolicyJobSummary, ScoringResult,
} from "./types";
import { jobStatusLabel, policyModeLabel } from "./display-format";
import { EvaluationSummary } from "./EvaluationSummary";

export type DecisionView = "policy" | "evaluation" | "explanation";
type Props = {
  scoring: ScoringResult;
  view: DecisionView;
  canRun: boolean;
  canCancel: boolean;
  onOpenResult: (kind: DecisionView, id: string) => void;
};

const DONE = new Set(["Succeeded", "Failed", "Cancelled"]);

/** Scoring Result를 변경하지 않고 정책·평가·개별 점수 설명을 재실행한다. */
export function MLDecisionPanel({ scoring, view, canRun, canCancel, onOpenResult }: Props) {
  const [mode, setMode] = useState<"fixed" | "quantile">("quantile");
  const [fixed, setFixed] = useState(0.5);
  const [fraction, setFraction] = useState(0.05);
  const [rowIndex, setRowIndex] = useState(0);
  const [policyJob, setPolicyJob] = useState<MLPolicyJob | null>(null);
  const [policy, setPolicy] = useState<DecisionPolicyRevision | null>(null);
  const [policyIdDraft, setPolicyIdDraft] = useState("");
  const [loadingPolicy, setLoadingPolicy] = useState(false);
  const [evaluationJob, setEvaluationJob] = useState<MLEvaluationJob | null>(null);
  const [evaluation, setEvaluation] = useState<EvaluationRevision | null>(null);
  const [explanationJob, setExplanationJob] = useState<MLExplanationJob | null>(null);
  const [explanation, setExplanation] = useState<ExplanationResult | null>(null);
  const [error, setError] = useState("");
  const policyHistory = usePagedJobs<MLPolicyJobSummary>("/api/ml/policies/jobs");
  const evaluationHistory = usePagedJobs<MLEvaluationJobSummary>("/api/ml/evaluations/jobs");
  const explanationHistory = usePagedJobs<MLExplanationJobSummary>("/api/ml/explanations/jobs");

  useEffect(() => {
    let alive = true;
    const reads = view === "policy" ? [policyHistory.refresh()] :
      view === "evaluation" ? [evaluationHistory.refresh(), policyHistory.refresh()] :
      [explanationHistory.refresh()];
    void Promise.all(reads).catch(e => { if (alive) setError(formatError(e)); });
    return () => { alive = false; };
  }, [scoring.id, view]);

  // 사용자가 별도 작업을 시작하는 동안에도 각 Job의 상태를 독립적으로 갱신한다.
  useEffect(() => {
    if (!policyJob || DONE.has(policyJob.status)) return;
    let active = true;
    const timer = setInterval(() => {
      void request<MLPolicyJob>(`/api/ml/policies/jobs/${policyJob.id}`)
        .then(j => { if (active) setPolicyJob(j); })
        .catch(e => { if (active) setError(formatError(e)); });
    }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [policyJob?.id, policyJob?.status]);

  useEffect(() => {
    let alive = true;
    const id = policyJob?.result?.policy_revision_id;
    if (policyJob?.status === "Succeeded" && typeof id === "string") {
      void request<DecisionPolicyRevision>(`/api/ml/policies/${id}`)
        .then(x => { if (alive) setPolicy(x); })
        .catch(e => { if (alive) setError(formatError(e)); });
    }
    return () => { alive = false; };
  }, [policyJob?.status, policyJob?.result?.policy_revision_id]);

  useEffect(() => {
    if (!evaluationJob || DONE.has(evaluationJob.status)) return;
    let active = true;
    const timer = setInterval(() => {
      void request<MLEvaluationJob>(`/api/ml/evaluations/jobs/${evaluationJob.id}`)
        .then(j => { if (active) setEvaluationJob(j); })
        .catch(e => { if (active) setError(formatError(e)); });
    }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [evaluationJob?.id, evaluationJob?.status]);

  useEffect(() => {
    let alive = true;
    const id = evaluationJob?.result?.evaluation_revision_id;
    if (evaluationJob?.status === "Succeeded" && typeof id === "string") {
      void request<EvaluationRevision>(`/api/ml/evaluations/${id}`)
        .then(x => { if (alive) setEvaluation(x); })
        .catch(e => { if (alive) setError(formatError(e)); });
    }
    return () => { alive = false; };
  }, [evaluationJob?.status, evaluationJob?.result?.evaluation_revision_id]);

  useEffect(() => {
    if (!explanationJob || DONE.has(explanationJob.status)) return;
    let active = true;
    const timer = setInterval(() => {
      void request<MLExplanationJob>(`/api/ml/explanations/jobs/${explanationJob.id}`)
        .then(j => { if (active) setExplanationJob(j); })
        .catch(e => { if (active) setError(formatError(e)); });
    }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [explanationJob?.id, explanationJob?.status]);

  useEffect(() => {
    let alive = true;
    const id = explanationJob?.result?.explanation_result_id;
    if (explanationJob?.status === "Succeeded" && typeof id === "string") {
      void request<ExplanationResult>(`/api/ml/explanations/${id}`)
        .then(x => { if (alive) setExplanation(x); })
        .catch(e => { if (alive) setError(formatError(e)); });
    }
    return () => { alive = false; };
  }, [explanationJob?.status, explanationJob?.result?.explanation_result_id]);

  useEffect(() => {
    if (policyJob && DONE.has(policyJob.status)) {
      void policyHistory.refresh().catch(e => setError(formatError(e)));
    }
  }, [policyJob?.id, policyJob?.status]);

  useEffect(() => {
    if (evaluationJob && DONE.has(evaluationJob.status)) {
      void evaluationHistory.refresh().catch(e => setError(formatError(e)));
    }
  }, [evaluationJob?.id, evaluationJob?.status]);

  useEffect(() => {
    if (explanationJob && DONE.has(explanationJob.status)) {
      void explanationHistory.refresh().catch(e => setError(formatError(e)));
    }
  }, [explanationJob?.id, explanationJob?.status]);

  async function restorePolicy(id: string) {
    try {
      setPolicy(null);
      setEvaluation(null);
      setEvaluationJob(null);
      setPolicyJob(await request<MLPolicyJob>(`/api/ml/policies/jobs/${encodeURIComponent(id)}`));
    } catch (e) { setError(formatError(e)); }
  }

  async function loadPolicyById() {
    const identifier = policyIdDraft.trim();
    if (!identifier) return;
    setLoadingPolicy(true);
    setPolicyJob(null);
    setPolicy(null);
    setError("");
    try {
      const loaded = await request<DecisionPolicyRevision>(
        `/api/ml/policies/${encodeURIComponent(identifier)}`,
      );
      setPolicy(loaded);
    } catch (e) { setError(formatError(e)); }
    finally { setLoadingPolicy(false); }
  }

  async function restoreEvaluation(id: string) {
    try {
      setEvaluation(null);
      setEvaluationJob(await request<MLEvaluationJob>(
        `/api/ml/evaluations/jobs/${encodeURIComponent(id)}`,
      ));
    } catch (e) { setError(formatError(e)); }
  }

  async function restoreExplanation(id: string) {
    try {
      setExplanation(null);
      setExplanationJob(await request<MLExplanationJob>(
        `/api/ml/explanations/jobs/${encodeURIComponent(id)}`,
      ));
    } catch (e) { setError(formatError(e)); }
  }

  async function cancelPolicy() {
    if (!canCancel || !policyJob || DONE.has(policyJob.status)) return;
    try {
      const updated = await request<MLPolicyJob>(
        `/api/ml/policies/jobs/${encodeURIComponent(policyJob.id)}/cancel`,
        { method: "POST" },
      );
      setPolicyJob(current => current?.id === updated.id ? updated : current);
    } catch (e) { setError(formatError(e)); }
  }

  async function cancelEvaluation() {
    if (!canCancel || !evaluationJob || DONE.has(evaluationJob.status)) return;
    try {
      const updated = await request<MLEvaluationJob>(
        `/api/ml/evaluations/jobs/${encodeURIComponent(evaluationJob.id)}/cancel`,
        { method: "POST" },
      );
      setEvaluationJob(current => current?.id === updated.id ? updated : current);
    } catch (e) { setError(formatError(e)); }
  }

  async function cancelExplanation() {
    if (!canCancel || !explanationJob || DONE.has(explanationJob.status)) return;
    try {
      const updated = await request<MLExplanationJob>(
        `/api/ml/explanations/jobs/${encodeURIComponent(explanationJob.id)}/cancel`,
        { method: "POST" },
      );
      setExplanationJob(current => current?.id === updated.id ? updated : current);
    } catch (e) { setError(formatError(e)); }
  }

  async function createPolicy() {
    setError("");
    setPolicy(null);
    setEvaluation(null);
    setEvaluationJob(null);
    try {
      const job = await submitJob<MLPolicyJob>("/api/ml/policies/jobs", {
        scoring_result_id: scoring.id,
        mode,
        threshold: mode === "fixed" ? fixed : null,
        alert_fraction: mode === "quantile" ? fraction : null,
      });
      setPolicyJob(job);
    } catch (e) { setError(formatError(e)); }
  }

  async function createEvaluation() {
    setError("");
    setEvaluation(null);
    try {
      setEvaluationJob(await submitJob<MLEvaluationJob>("/api/ml/evaluations/jobs", {
        scoring_result_id: scoring.id,
        policy_revision_id: policy?.id ?? null,
      }));
    } catch (e) { setError(formatError(e)); }
  }

  async function createExplanation() {
    setError("");
    setExplanation(null);
    try {
      setExplanationJob(await submitJob<MLExplanationJob>("/api/ml/explanations/jobs", {
        scoring_result_id: scoring.id, row_index: rowIndex,
      }));
    } catch (e) { setError(formatError(e)); }
  }

  const metric = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v) ? v.toFixed(5) : "미정의";
  const details = explanation?.output;
  const sensitivities = Array.isArray(details?.feature_sensitivities)
    ? details.feature_sensitivities as Array<Record<string, unknown>> : [];
  return <div className="steps" role="region" aria-label="임계값 정책과 평가">
    <h3>{view === "policy" ? "판정 정책" : view === "evaluation" ? "평가" : "설명"}</h3>
    {error && <p role="alert">{error}</p>}
    <p className="hint">선택한 점수 결과({scoring.row_count.toLocaleString("ko-KR")}건)를 재사용합니다. 모델 재학습이나 재점수화를 수행하지 않습니다.</p>
    {view === "policy" && <>
    <p className="hint">분위수 정책은 선택한 점수 결과에서 임계값을 산정하며, 별도 보정·검증 데이터의 근거가 아닙니다.</p>
    <div className="form-row">
      <label>임계값 정책
        <select aria-label="임계값 정책" value={mode}
          onChange={e => setMode(e.target.value as "fixed" | "quantile")}>
          <option value="fixed">고정 임계값</option>
          <option value="quantile">현재 점수 결과의 상위 분위수</option>
        </select>
      </label>
      {mode === "fixed" ? <label>고정 점수 경계
        <input type="number" step="any" aria-label="고정 점수 경계" value={fixed}
          onChange={e => setFixed(Number(e.target.value))} />
      </label> : <label>상위 경보 비율
        <input type="number" step="0.01" min="0.01" max="0.99" aria-label="상위 경보 비율"
          value={fraction} onChange={e => setFraction(Number(e.target.value))} />
      </label>}
    </div>
    <button disabled={!canRun || (policyJob !== null && !DONE.has(policyJob.status)) ||
      (mode === "fixed" ? !Number.isFinite(fixed) : !(fraction > 0 && fraction < 1))}
      onClick={() => void createPolicy()}>새 정책 판본 생성</button>
    {policyJob && <p role="status">정책 Job · {jobStatusLabel(policyJob.status)}
      {policyJob.cancel_requested && policyJob.status === "Running" && " · 취소 요청됨"}
    </p>}
    {canCancel && policyJob && !DONE.has(policyJob.status) && !policyJob.cancel_requested &&
      <button className="secondary" onClick={() => void cancelPolicy()}>정책 취소 요청</button>}
    {policyJob?.status === "Failed" && <p role="alert">정책 실패 · {policyJob.error ?? policyJob.error_code}</p>}
    <div className="job-list">
      <strong>이 점수의 정책 이력</strong>
      {policyHistory.items.filter(job => job.spec.scoring_result_id === scoring.id)
        .map(job => <button key={job.id} className="secondary"
          onClick={() => void restorePolicy(job.id)}>
          정책 {job.id.slice(0, 8)} · {jobStatusLabel(job.status)}
        </button>)}
      {policyHistory.nextCursor && <button className="secondary"
        onClick={() => void policyHistory.loadMore().catch(e => setError(formatError(e)))}>
        이전 정책 더 보기
      </button>}
    </div>
    {policy && <div className="result">
      <p>{policyModeLabel(policy.mode)} · 적용 임계값 {metric(policy.threshold)} · 이상 판정
        {" "}{String(policy.decision_summary.predicted_anomalies ?? "—")}건</p>
      <p className="hint">동점이 있으면 분위수로 설정한 경보 비율과 실제 판정 비율은 다를 수 있습니다.</p>
      <button type="button" className="secondary"
        onClick={() => onOpenResult("policy", policy.id)}>정책 판본 상세 보기 →</button>
    </div>}
    </>}
    {view === "evaluation" && <>
    <label>평가에 적용할 정책 (선택)
      <select aria-label="평가 정책 판본 선택" disabled={loadingPolicy} value={policyJob?.id ?? ""}
        onChange={event => {
          setPolicyIdDraft("");
          if (event.target.value) void restorePolicy(event.target.value);
          else { setPolicyJob(null); setPolicy(null); }
        }}>
        <option value="">정책 없음 · 레이블이 있으면 순위 지표만 평가</option>
        {policyHistory.items.filter(job =>
          job.status === "Succeeded" && job.spec.scoring_result_id === scoring.id &&
          typeof job.result?.policy_revision_id === "string"
        ).map(job => <option key={job.id} value={job.id}>
          정책 {String(job.result?.policy_revision_id).slice(0, 12)}
        </option>)}
      </select>
    </label>
    <div className="form-row">
      <label>기존 정책 판본 ID (다른 점수 결과의 정책도 입력 가능)
        <input aria-label="재사용할 정책 판본 ID" disabled={loadingPolicy} value={policyIdDraft}
          placeholder="호환되는 판정 정책 판본 ID"
          onChange={event => {
            setPolicyIdDraft(event.target.value);
            setPolicyJob(null);
            setPolicy(null);
          }} />
      </label>
      <button type="button" className="secondary"
        disabled={!policyIdDraft.trim() || loadingPolicy}
        onClick={() => void loadPolicyById()}>
        {loadingPolicy ? "정책 조회 중" : "정책 판본 조회"}
      </button>
    </div>
    <p className="hint">다른 점수 결과의 정책도 호환된다면 적용할 수 있습니다.
      모델·Feature·점수 의미·접근 권한은 제출 시 API가 검증합니다.</p>
    {policyHistory.nextCursor && <button type="button" className="secondary"
      onClick={() => void policyHistory.loadMore().catch(e => setError(formatError(e)))}>
      이전 정책 더 보기
    </button>}
    {policy && <p className="hint" title={policy.id}>선택한 정책: {policyModeLabel(policy.mode)} · 점수 경계 {metric(policy.threshold)}</p>}
    <button disabled={!canRun || loadingPolicy || (policyJob !== null && policy === null) ||
      (evaluationJob !== null && !DONE.has(evaluationJob.status))}
      onClick={() => void createEvaluation()}>평가 판본 생성</button>
    {policyJob && !policy && <p role="status">선택한 정책 판본을 확인하는 중입니다.
      완료 전에는 평가를 제출할 수 없습니다.</p>}
    {evaluationJob && <p role="status">평가 작업 · {jobStatusLabel(evaluationJob.status)}
      {evaluationJob.cancel_requested && evaluationJob.status === "Running" && " · 취소 요청됨"}
    </p>}
    {canCancel && evaluationJob && !DONE.has(evaluationJob.status) && !evaluationJob.cancel_requested &&
      <button className="secondary" onClick={() => void cancelEvaluation()}>평가 취소 요청</button>}
    {evaluationJob?.status === "Failed" && <p role="alert">평가 실패 · {evaluationJob.error ?? evaluationJob.error_code}</p>}
    <div className="job-list">
      <strong>이 점수의 평가 이력</strong>
      {evaluationHistory.items.filter(job => job.spec.scoring_result_id === scoring.id)
        .map(job => <button key={job.id} className="secondary"
          onClick={() => void restoreEvaluation(job.id)}>
          평가 {job.id.slice(0, 8)} · {jobStatusLabel(job.status)}
        </button>)}
      {evaluationHistory.nextCursor && <button className="secondary"
        onClick={() => void evaluationHistory.loadMore().catch(e => setError(formatError(e)))}>
        이전 평가 더 보기
      </button>}
    </div>
    {evaluation && <div className="result">
      <EvaluationSummary evaluation={evaluation} />
      {evaluation.policy_revision_id && evaluation.policy_revision_id !== policy?.id &&
        <p className="hint">이 평가의 기준 정책은 현재 선택된 정책과 다를 수 있습니다.</p>}
      <p className="hint">동일 평가 점수에서 임계값을 선택하고 평가한 지표는 독립 검증셋 성능을 보증하지 않습니다. 레이블이 없거나 정의할 수 없는 경우에는 미정의로 표시합니다.</p>
      <button type="button" className="secondary"
        onClick={() => onOpenResult("evaluation", evaluation.id)}>평가 판본 상세 보기 →</button>
    </div>}
    </>}
    {view === "explanation" && <>
    <h3>개별 이상 점수 설명</h3>
    <label>결과 내 행 번호
      <input type="number" aria-label="설명할 행 번호" min="0"
        max={Math.max(0, scoring.row_count - 1)} value={rowIndex}
        onChange={e => setRowIndex(Number(e.target.value))} />
    </label>
    <button disabled={!canRun || !Number.isInteger(rowIndex) || rowIndex < 0 ||
      rowIndex >= scoring.row_count || (explanationJob !== null && !DONE.has(explanationJob.status))}
      onClick={() => void createExplanation()}>점수 민감도 설명 실행</button>
    {explanationJob && <p role="status">설명 작업 · {jobStatusLabel(explanationJob.status)}
      {explanationJob.cancel_requested && explanationJob.status === "Running" && " · 취소 요청됨"}
    </p>}
    {canCancel && explanationJob && !DONE.has(explanationJob.status) && !explanationJob.cancel_requested &&
      <button className="secondary" onClick={() => void cancelExplanation()}>설명 취소 요청</button>}
    {explanationJob?.status === "Failed" && <p role="alert">설명 실패 · {explanationJob.error ?? explanationJob.error_code}</p>}
    <div className="job-list">
      <strong>이 점수의 설명 이력</strong>
      {explanationHistory.items.filter(job => job.spec.scoring_result_id === scoring.id)
        .map(job => <button key={job.id} className="secondary"
          onClick={() => void restoreExplanation(job.id)}>
          설명 {job.id.slice(0, 8)} · {jobStatusLabel(job.status)}
        </button>)}
      {explanationHistory.nextCursor && <button className="secondary"
        onClick={() => void explanationHistory.loadMore().catch(e => setError(formatError(e)))}>
        이전 설명 더 보기
      </button>}
    </div>
    {explanation && <div className="result">
      <p>설명 결과 · 기준 이상 점수 {metric(details?.score)}</p>
      <table>
        <caption>Feature를 학습 참조 중앙값으로 바꾸었을 때의 점수 변화</caption>
        <thead><tr><th>Feature</th><th>실제값</th><th>참조 중앙값</th>
          <th>이상 점수 변화</th></tr></thead>
        <tbody>{sensitivities.map((x, i) => <tr key={i}>
          <th>{String(x.feature)}</th><td>{metric(x.actual_value)}</td>
          <td>{metric(x.reference_median)}</td>
          <td>{metric(x.anomaly_score_delta)}</td>
        </tr>)}</tbody>
      </table>
      <p className="hint">이 결과는 Feature 치환에 대한 모델의 국소 민감도입니다. SHAP·LIME이나 가법적 기여도, 실제 이상 발생 원인의 증명이 아닙니다.</p>
      <button type="button" className="secondary"
        onClick={() => onOpenResult("explanation", explanation.id)}>설명 결과 상세 보기 →</button>
    </div>}
    </>}
  </div>;
}
