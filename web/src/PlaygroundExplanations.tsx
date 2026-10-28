/** 완료된 Playground 입력의 두 설명 방법을 독립 Job으로 탐구한다. */
import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { ShapContributionChart } from "./PipelineShapExplanation";
import type { ExplanationResult, MLExplanationJob, MLExplanationJobSummary, MLPlaygroundJob } from "./types";

type Method = "permutation_shap_median_v1" | "median_reference_feature_replacement";
type PlaygroundResult = ExplanationResult & { playground_job_id?: string | null };
type JobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
export const formatPlaygroundSensitivity = (value: number) =>
  value !== 0 && Math.abs(value) < 1e-8
    ? value.toExponential(3)
    : value.toLocaleString("ko-KR", { maximumFractionDigits: 8 });
type Sensitivity = {
  feature: string; actual_value: number | null; reference_median: number;
  score_if_replaced: number; anomaly_score_delta: number;
};

/** 부호는 원래 점수 - 한 Feature만 기준값으로 치환한 점수다. */
export function PlaygroundSensitivity({ result }: { result: PlaygroundResult }) {
  const rows = (Array.isArray(result.output.feature_sensitivities)
    ? result.output.feature_sensitivities as Sensitivity[] : [])
    .filter(entry => Number.isFinite(entry.anomaly_score_delta))
    .sort((a, b) => Math.abs(b.anomaly_score_delta) - Math.abs(a.anomaly_score_delta));
  const max = Math.max(0, ...rows.map(entry => Math.abs(entry.anomaly_score_delta)));
  const allZero = rows.length > 0 && rows.every(entry => entry.anomaly_score_delta === 0);
  return <div className="playground-explanation-detail">
    <p className="hint">현재 점수에서 특정 Feature 하나만 Train 중앙값으로 바꿔 다시 점수화한 실제 변화입니다.
      기여도 합이나 인과관계가 아니며, 다른 Feature는 그대로 유지합니다.</p>
    {allZero ? <div className="pipeline-shap-zero" role="status">
      <strong>Feature를 학습 중앙값으로 바꿔도 점수가 달라지지 않습니다.</strong>
      <p>현재 입력이 학습 중앙값으로 구성된 기준 입력이라면 자연스러운 결과입니다.
        다른 입력값으로 계산한 뒤 민감도를 다시 살펴볼 수 있습니다.</p>
    </div> : <div className="playground-sensitivity-bars">
      {rows.slice(0, 8).map(item => <div
        key={item.feature} className={item.anomaly_score_delta < 0 ? "negative" : "positive"}>
        <span>{activityFeatureLabel(item.feature)}</span>
        <strong>{item.anomaly_score_delta > 0 ? "+" : ""}{formatPlaygroundSensitivity(item.anomaly_score_delta)}</strong>
        <div className="playground-sensitivity-track" aria-hidden="true">
          <i style={{ width: max ? `${Math.abs(item.anomaly_score_delta) / max * 100}%` : "0%" }} />
        </div>
      </div>)}
    </div>}
    <details><summary>전체 {rows.length}개 Feature별 변경 결과</summary>
      <div className="table-scroll"><table>
        <thead><tr><th>Feature</th><th>원래 값</th><th>Train 중앙값</th>
          <th>치환 후 점수</th><th>원래 점수 − 치환 점수</th></tr></thead>
        <tbody>{rows.map(item => <tr key={item.feature}>
          <td>{activityFeatureLabel(item.feature)}</td>
          <td>{item.actual_value === null ? "결측" : formatPlaygroundSensitivity(item.actual_value)}</td>
          <td>{formatPlaygroundSensitivity(item.reference_median)}</td>
          <td>{formatPlaygroundSensitivity(item.score_if_replaced)}</td>
          <td>{formatPlaygroundSensitivity(item.anomaly_score_delta)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </div>;
}

function MethodExplanation({ source, method, canRun, canCancel }: {
  source: MLPlaygroundJob; method: Method; canRun: boolean; canCancel: boolean;
}) {
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [lookup, setLookup] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const title = method === "permutation_shap_median_v1" ? "SHAP 기여도" : "Feature 치환 민감도";
  const score = source.result?.anomaly_score;
  const modelId = source.spec.model_revision_id;

  useEffect(() => {
    let live = true;
    setLookup("loading");
    void request<JobPage>("/api/ml/explanations/jobs?limit=100")
      .then(async page => {
        const found = page.items.find(item =>
          item.spec.playground_job_id === source.id && item.spec.method === method);
        if (!live) return;
        if (!found) { setLookup("ready"); return; }
        const existing = await request<MLExplanationJob>(
          "/api/ml/explanations/jobs/" + encodeURIComponent(found.id));
        if (live) { setJob(existing); setLookup("ready"); }
      }).catch(() => { if (live) setLookup("error"); });
    return () => { live = false; };
  }, [source.id, method, retry]);

  useEffect(() => {
    if (!job || terminal.has(job.status)) return;
    let live = true;
    const timer = setInterval(() => {
      void request<MLExplanationJob>("/api/ml/explanations/jobs/" + encodeURIComponent(job.id))
        .then(next => { if (live) setJob(next); })
        .catch(cause => { if (live) setError(formatError(cause)); });
    }, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [job?.id, job?.status]);

  useEffect(() => {
    if (job?.status !== "Succeeded") return;
    const id = job.result?.explanation_result_id;
    if (typeof id !== "string") {
      setError("완료된 설명 결과를 확인할 수 없습니다.");
      return;
    }
    let live = true;
    void request<PlaygroundResult>("/api/ml/explanations/" + encodeURIComponent(id))
      .then(value => {
        if (!live) return;
        const outputScore = value.output.score;
        if (value.playground_job_id !== source.id ||
            value.model_revision_id !== modelId || value.method !== method ||
            typeof score !== "number" || typeof outputScore !== "number" ||
            !Number.isFinite(outputScore) ||
            Math.abs(outputScore - score) > 1e-10 + 1e-9 * Math.abs(score)) {
          setError("설명 결과가 선택한 입력 Job·모델 판본·점수와 일치하지 않습니다.");
          return;
        }
        setResult(value);
        setError("");
      }).catch(cause => { if (live) setError(formatError(cause)); });
    return () => { live = false; };
  }, [job?.id, job?.status, job?.result?.explanation_result_id, source.id, score, modelId, method]);

  async function run() {
    if (!canRun || busy || lookup !== "ready" || (job && !terminal.has(job.status))) return;
    setBusy(true); setError(""); setJob(null); setResult(null);
    try {
      setJob(await submitJob<MLExplanationJob>("/api/ml/explanations/jobs", {
        playground_job_id: source.id, method,
      }));
    } catch (cause) { setError(formatError(cause)); }
    finally { setBusy(false); }
  }
  async function cancel() {
    if (!job || terminal.has(job.status) || !canCancel || busy) return;
    setBusy(true); setError("");
    try {
      setJob(await request<MLExplanationJob>(
        "/api/ml/explanations/jobs/" + encodeURIComponent(job.id) + "/cancel",
        { method: "POST" }));
    } catch (cause) { setError(formatError(cause)); }
    finally { setBusy(false); }
  }
  return <section className="playground-explanation-method">
    <div className="model-lab-feature-group-heading"><strong>{title}</strong>
      <span>{job ? job.status : "선택적으로 계산"}</span></div>
    <p className="hint">{method === "permutation_shap_median_v1"
      ? "실제 점수에 대한 기여도를 한 개의 합성 Train 중앙값 입력을 기준으로 설명합니다. 사건 원인이나 확률이 아닙니다."
      : "Feature 하나를 참조 중앙값으로 치환할 때 점수가 어떻게 달라지는지 측정합니다."}</p>
    {lookup === "loading" && <p role="status">이 입력의 기존 설명을 확인 중입니다.</p>}
    {lookup === "error" && <button type="button" className="secondary"
      onClick={() => setRetry(current => current + 1)}>기존 설명 다시 조회</button>}
    {canRun && (!result || job?.status === "Failed" || job?.status === "Cancelled") &&
      <button type="button" disabled={busy || lookup !== "ready" ||
        Boolean(job && !terminal.has(job.status))}
        onClick={() => void run()}>{busy ? "설명 요청 중…" : `${title} 계산`}</button>}
    {!canRun && !job && <p className="hint">설명 계산 권한이 없습니다.</p>}
    {job && !terminal.has(job.status) && <p role="status">{title} · {job.status}
      {job.cancel_requested ? " · 취소 요청됨" : ""}</p>}
    {job && !terminal.has(job.status) && canCancel && <button type="button" className="secondary"
      disabled={busy || job.cancel_requested} onClick={() => void cancel()}>
      설명 계산 취소 요청</button>}
    {job?.status === "Failed" && <p role="alert">{title} 실행 실패 · {job.error_code ?? "실행 오류"}</p>}
    {job?.status === "Cancelled" && <p role="status">{title} 계산이 취소됐습니다.</p>}
    {error && <p role="alert">{error}</p>}
    {result && <div>
      {method === "permutation_shap_median_v1"
        ? <ShapContributionChart result={result}
            valueLabel="제출한 입력값" inputLabel="이 제출 입력" />
        : <PlaygroundSensitivity result={result} />}
      {canRun && <button type="button" className="secondary"
        disabled={busy} onClick={() => void run()}>{title} 다시 계산</button>}
    </div>}
  </section>;
}

export function PlaygroundExplanations({ source, canRun, canCancel, shapSupported = true }: {
  source: MLPlaygroundJob; canRun: boolean; canCancel: boolean;
  shapSupported?: boolean;
}) {
  if (source.status !== "Succeeded" || typeof source.result?.anomaly_score !== "number")
    return null;
  return <section className="playground-explanations" aria-label="단일 입력 모델 설명">
    <h4>이 입력의 모델 설명</h4>
    <p className="hint">실제 완료된 입력 Job을 기준으로 계산합니다. 값을 편집해도 과거 설명을
      현재 입력의 설명으로 취급하지 않습니다. 두 방법은 서로 다른 질문에 답합니다.</p>
    {shapSupported
      ? <MethodExplanation key={source.id + "-shap"} source={source}
          method="permutation_shap_median_v1" canRun={canRun} canCancel={canCancel} />
      : <p className="hint">현재 Permutation SHAP은 최대 64개 Feature를 지원합니다.
          이 모델에서는 Feature 치환 민감도를 사용할 수 있습니다.</p>}
    <MethodExplanation key={source.id + "-sensitivity"} source={source}
      method="median_reference_feature_replacement" canRun={canRun} canCancel={canCancel} />
  </section>;
}
