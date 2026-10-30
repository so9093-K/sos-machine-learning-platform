/** 사용자가 요청한 완료 입력의 SHAP 점수 설명. 값 변경 실험은 입력 화면에서 수행한다. */
import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { ShapContributionChart } from "./ShapExplanation";
import { jobStatusLabel } from "./display-format";
import type { ExplanationResult, MLExplanationJob, MLExplanationJobSummary, MLPlaygroundJob } from "./types";

const method = "permutation_shap_median_v1";
type PlaygroundResult = ExplanationResult & { playground_job_id?: string | null };
type JobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
function ShapExplanation({ source, canRun, canCancel }: {
  source: MLPlaygroundJob; canRun: boolean; canCancel: boolean;
}) {
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [lookup, setLookup] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const title = "SHAP 기여도";
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
      <span>{job ? jobStatusLabel(job.status) : "선택적으로 계산"}</span></div>
    <p className="hint">실제 점수에 대한 기여도를 한 개의 합성 Train 중앙값 입력을 기준으로 설명합니다. 사건 원인이나 확률이 아닙니다.</p>
    {lookup === "loading" && <p role="status">이 입력의 기존 설명을 확인 중입니다.</p>}
    {lookup === "error" && <button type="button" className="secondary"
      onClick={() => setRetry(current => current + 1)}>기존 설명 다시 조회</button>}
    {canRun && (!result || job?.status === "Failed" || job?.status === "Cancelled") &&
      <button type="button" disabled={busy || lookup !== "ready" ||
        Boolean(job && !terminal.has(job.status))}
        onClick={() => void run()}>{busy ? "설명 요청 중…" : `${title} 계산`}</button>}
    {!canRun && !job && <p className="hint">설명 계산 권한이 없습니다.</p>}
    {job && !terminal.has(job.status) && <p role="status">{title} · {jobStatusLabel(job.status)}
      {job.cancel_requested ? " · 취소 요청됨" : ""}</p>}
    {job && !terminal.has(job.status) && canCancel && <button type="button" className="secondary"
      disabled={busy || job.cancel_requested} onClick={() => void cancel()}>
      설명 계산 취소 요청</button>}
    {job?.status === "Failed" && <p role="alert">{title} 실행 실패 · {job.error_code ?? "실행 오류"}</p>}
    {job?.status === "Cancelled" && <p role="status">{title} 계산이 취소됐습니다.</p>}
    {error && <p role="alert">{error}</p>}
    {result && <div>
      <ShapContributionChart result={result} valueLabel="제출한 입력값" inputLabel="이 제출 입력" />
      {canRun && <button type="button" className="secondary"
        disabled={busy} onClick={() => void run()}>{title} 다시 계산</button>}
    </div>}
  </section>;
}

export function PlaygroundExplanations({ source, canRun, canCancel, shapSupported = true }: {
  source: MLPlaygroundJob; canRun: boolean; canCancel: boolean;
  shapSupported?: boolean;
}) {
  if (source.status !== "Succeeded" || typeof source.result?.anomaly_score !== "number") return null;
  return shapSupported ? <ShapExplanation key={source.id} source={source}
    canRun={canRun} canCancel={canCancel} /> : <p className="hint">이 모델의 입력 수는 SHAP 계산 범위를 넘습니다.</p>;
}
