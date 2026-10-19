import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import type {
  DecisionPolicyRevision, EvaluationRevision, ExplanationResult,
  MLEvaluationJob, MLEvaluationJobSummary, MLExplanationJob,
  MLExplanationJobSummary, MLPolicyJob, MLPolicyJobSummary, ScoringResult,
} from "./types";

type Props = { scoring: ScoringResult; canRun: boolean };

const DONE = new Set(["Succeeded", "Failed", "Cancelled"]);

/** Scoring Result를 변경하지 않고 정책·평가·개별 점수 설명을 재실행한다. */
export function MLDecisionPanel({ scoring, canRun }: Props) {
  const [mode, setMode] = useState<"fixed" | "quantile">("quantile");
  const [fixed, setFixed] = useState(0.5);
  const [fraction, setFraction] = useState(0.05);
  const [rowIndex, setRowIndex] = useState(0);
  const [policyJob, setPolicyJob] = useState<MLPolicyJob | null>(null);
  const [policy, setPolicy] = useState<DecisionPolicyRevision | null>(null);
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
    void Promise.all([
      policyHistory.refresh(), evaluationHistory.refresh(), explanationHistory.refresh(),
    ]).catch(e => { if (alive) setError(formatError(e)); });
    return () => { alive = false; };
  }, [scoring.id]);

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
    <h4>Decision Policy · Evaluation</h4>
    {error && <p role="alert">{error}</p>}
    <p className="hint">이 Scoring Result를 재사용하므로 모델을 다시 학습하거나 점수를 재계산하지 않습니다.</p>
    <div className="form-row">
      <label>임계값 정책
        <select aria-label="임계값 정책" value={mode}
          onChange={e => setMode(e.target.value as "fixed" | "quantile")}>
          <option value="fixed">고정 임계값</option>
          <option value="quantile">참조 점수의 상위 분위수</option>
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
    {policyJob && <p role="status">정책 Job · {policyJob.status}</p>}
    <div className="job-list">
      <strong>이 점수의 정책 이력</strong>
      {policyHistory.items.filter(job => job.spec.scoring_result_id === scoring.id)
        .map(job => <button key={job.id} className="secondary"
          onClick={() => void restorePolicy(job.id)}>
          정책 {job.id.slice(0, 8)} · {job.status}
        </button>)}
      {policyHistory.nextCursor && <button className="secondary"
        onClick={() => void policyHistory.loadMore().catch(e => setError(formatError(e)))}>
        이전 정책 더 보기
      </button>}
    </div>
    {policy && <div className="result">
      <p>Decision Policy Revision · {policy.id}</p>
      <p>적용 임계값: {metric(policy.threshold)} · 이상 판정 수:
        {String(policy.decision_summary.predicted_anomalies ?? "—")}</p>
      <p className="hint">동점이 있으면 분위수로 설정한 경보 비율과 실제 판정 비율은 다를 수 있습니다.</p>
    </div>}
    <button disabled={!canRun || (evaluationJob !== null && !DONE.has(evaluationJob.status))}
      onClick={() => void createEvaluation()}>독립 Evaluation 판본 생성</button>
    {evaluationJob && <p role="status">Evaluation Job · {evaluationJob.status}</p>}
    <div className="job-list">
      <strong>이 점수의 평가 이력</strong>
      {evaluationHistory.items.filter(job => job.spec.scoring_result_id === scoring.id)
        .map(job => <button key={job.id} className="secondary"
          onClick={() => void restoreEvaluation(job.id)}>
          평가 {job.id.slice(0, 8)} · {job.status}
        </button>)}
      {evaluationHistory.nextCursor && <button className="secondary"
        onClick={() => void evaluationHistory.loadMore().catch(e => setError(formatError(e)))}>
        이전 평가 더 보기
      </button>}
    </div>
    {evaluation && <div className="result">
      <p>Evaluation Revision · {evaluation.id} · 레이블 수준: {evaluation.label_coverage}</p>
      <div className="cards">
        <div className="stat"><span>Precision</span><strong>{metric(evaluation.decision_metrics.precision)}</strong></div>
        <div className="stat"><span>Recall</span><strong>{metric(evaluation.decision_metrics.recall)}</strong></div>
        <div className="stat"><span>F1</span><strong>{metric(evaluation.decision_metrics.f1)}</strong></div>
      </div>
      <p className="hint">동일 평가 점수에서 임계값을 선택하고 평가한 지표는 독립 검증셋 성능을 보증하지 않습니다. 레이블이 없거나 정의할 수 없는 경우에는 미정의로 표시합니다.</p>
    </div>}
    <h4>개별 이상 점수 설명</h4>
    <label>결과 내 행 번호
      <input type="number" aria-label="설명할 행 번호" min="0"
        max={Math.max(0, scoring.row_count - 1)} value={rowIndex}
        onChange={e => setRowIndex(Number(e.target.value))} />
    </label>
    <button disabled={!canRun || !Number.isInteger(rowIndex) || rowIndex < 0 ||
      rowIndex >= scoring.row_count || (explanationJob !== null && !DONE.has(explanationJob.status))}
      onClick={() => void createExplanation()}>점수 민감도 설명 실행</button>
    {explanationJob && <p role="status">설명 Job · {explanationJob.status}</p>}
    <div className="job-list">
      <strong>이 점수의 설명 이력</strong>
      {explanationHistory.items.filter(job => job.spec.scoring_result_id === scoring.id)
        .map(job => <button key={job.id} className="secondary"
          onClick={() => void restoreExplanation(job.id)}>
          설명 {job.id.slice(0, 8)} · {job.status}
        </button>)}
      {explanationHistory.nextCursor && <button className="secondary"
        onClick={() => void explanationHistory.loadMore().catch(e => setError(formatError(e)))}>
        이전 설명 더 보기
      </button>}
    </div>
    {explanation && <div className="result">
      <p>Explanation · {explanation.id} · 기준 이상 점수 {metric(details?.score)}</p>
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
    </div>}
  </div>;
}
