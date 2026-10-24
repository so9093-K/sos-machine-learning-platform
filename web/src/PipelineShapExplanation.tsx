/** 기존 독립 설명 Job을 SHAP 방법으로 요청하고 해당 결과를 탐구한다. */
import { useEffect, useRef, useState } from "react";
import { DataChart } from "./DataChart";
import { formatError, request, submitJob } from "./api";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ExplanationResult, MLExplanationJob, MLExplanationJobSummary } from "./types";
import { matchingExplanationJob } from "./PipelineCandidateInvestigation";

const METHOD = "permutation_shap_median_v1";
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
type JobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };
type Contribution = { feature: string; shap_value: number; actual_value: number | null;
  reference_median: number; missing_imputed: boolean };

export function ShapContributionChart({ result }: { result: ExplanationResult }) {
  const output = result.output;
  const values = Array.isArray(output.feature_contributions)
    ? output.feature_contributions as Contribution[] : [];
  const contributions = values.filter(item => typeof item.feature === "string" &&
    typeof item.shap_value === "number" && Number.isFinite(item.shap_value))
    .sort((a, b) => Math.abs(b.shap_value) - Math.abs(a.shap_value));
  if (output.method !== METHOD || !contributions.length) {
    return <p role="alert">SHAP 기여도 결과의 형식을 확인할 수 없습니다.</p>;
  }
  const key = (value: unknown) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 7 }) : "미정의";
  const top = contributions.slice(0, 12).reverse();
  const option = {
    aria: { enabled: true },
    tooltip: { trigger: "axis" },
    grid: { left: "26%", right: "10%", top: 16, bottom: 40 },
    xAxis: { type: "value" as const, name: "SHAP 점수 기여도" },
    yAxis: { type: "category" as const,
      data: top.map(x => x.feature),
      axisLabel: { width: 132, overflow: "truncate" as const } },
    series: [{ type: "bar" as const, name: "기준 점수 대비 기여",
      data: top.map(x => x.shap_value) }],
  };
  return <section aria-label="SHAP 모델 점수 기여도" className="pipeline-shap-result">
    <h6>모델 이상 점수에 대한 SHAP 기여도</h6>
    <p>기준 점수 <strong>{key(output.baseline_score)}</strong> +
      기여도 합 = 실제 이상 점수 <strong>{key(output.score)}</strong></p>
    <DataChart option={option} ariaLabel="실제 SOS 이상 점수 기준 Feature별 Permutation SHAP 기여도" />
    <p className="hint">양수는 단일 기준값 대비 모델 이상 점수를 높이는 방향, 음수는 낮추는 방향입니다.
      기준은 Train Feature 중앙값으로 구성한 합성 관측치 1행입니다.
      전체 Train 분포나 실제 사건의 원인·확률을 뜻하지 않습니다.</p>
    <details><summary>전체 SHAP 값과 계산 근거</summary>
      <p>방법: Permutation SHAP · 출력: -score_samples · 배경 행 {key(output.baseline_row_count)}개 ·
        순열 반복 {key(output.permutation_rounds)}회 · Seed {key(output.sampling_seed)} ·
        SHAP 라이브러리 {String(output.shap_version ?? "미정의")}</p>
      <div className="table-scroll"><table>
        <thead><tr><th>Feature</th><th>관측값</th><th>Train 중앙값</th><th>점수 기여</th></tr></thead>
        <tbody>{contributions.map(item => <tr key={item.feature}>
          <th>{item.feature}</th><td>{item.missing_imputed ? "결측 대체" : key(item.actual_value)}</td>
          <td>{key(item.reference_median)}</td><td>{key(item.shap_value)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </section>;
}

export function PipelineShapExplanation({ candidate, scoringResultId, canRun, canCancel, onOpenResource }: {
  candidate: { row_index: number };
  scoringResultId: string;
  canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<ExplanationResult | null>(null);
  const [lookup, setLookup] = useState<"loading" | "ready" | "error">("loading");
  const [lookupRetry, setLookupRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const submitted = useRef(false);

  useEffect(() => {
    let active = true;
    setLookup("loading");
    void request<JobPage>("/api/ml/explanations/jobs?limit=100")
      .then(page => {
        if (!active || submitted.current) return;
        const prior = matchingExplanationJob(page.items, scoringResultId, candidate.row_index, METHOD);
        if (!prior) { setLookup("ready"); return; }
        return request<MLExplanationJob>("/api/ml/explanations/jobs/" + encodeURIComponent(prior.id))
          .then(next => {
            if (!active || submitted.current) return;
            if (next.spec.scoring_result_id !== scoringResultId ||
                next.spec.row_index !== candidate.row_index || next.spec.method !== METHOD) {
              throw new Error("기존 SHAP 작업과 선택한 후보가 일치하지 않습니다.");
            }
            setJob(next);
            setLookup("ready");
          });
      }).catch(() => { if (active && !submitted.current) setLookup("error"); });
    return () => { active = false; };
  }, [scoringResultId, candidate.row_index, lookupRetry]);

  useEffect(() => {
    if (!job || terminal.has(job.status) || error) return;
    let active = true;
    const interval = setInterval(() => {
      void request<MLExplanationJob>("/api/ml/explanations/jobs/" + encodeURIComponent(job.id))
        .then(next => { if (active) setJob(next); })
        .catch(reason => { if (active) setError(formatError(reason)); });
    }, 2000);
    return () => { active = false; clearInterval(interval); };
  }, [job?.id, job?.status, error, retry]);

  useEffect(() => {
    if (job?.status !== "Succeeded") return;
    const id = job.result?.explanation_result_id;
    if (typeof id !== "string") { setError("완료된 SHAP 설명 결과를 찾지 못했습니다."); return; }
    let active = true;
    void request<ExplanationResult>("/api/ml/explanations/" + encodeURIComponent(id))
      .then(next => {
        if (!active) return;
        if (next.scoring_result_id !== scoringResultId ||
            next.row_index !== candidate.row_index || next.method !== METHOD) {
          setError("SHAP 결과가 선택한 관측치와 일치하지 않습니다.");
        } else { setResult(next); setError(""); }
      }).catch(reason => { if (active) setError(formatError(reason)); });
    return () => { active = false; };
  }, [job?.id, job?.status, job?.result?.explanation_result_id,
    scoringResultId, candidate.row_index, retry]);

  async function submit() {
    if (!canRun || busy || lookup !== "ready" || (job && !terminal.has(job.status))) return;
    submitted.current = true;
    setBusy(true); setJob(null); setResult(null); setError("");
    try {
      setJob(await submitJob<MLExplanationJob>("/api/ml/explanations/jobs", {
        scoring_result_id: scoringResultId, row_index: candidate.row_index, method: METHOD,
      }));
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }

  async function cancel() {
    if (!job || terminal.has(job.status) || job.cancel_requested || busy || !canCancel) return;
    setBusy(true); setError("");
    const id = job.id;
    try {
      const next = await request<MLExplanationJob>(
        "/api/ml/explanations/jobs/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
      setJob(previous => previous?.id === id ? next : previous);
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }

  return <div className="pipeline-shap-explanation">
    <h6>모델 판단 근거 자세히 보기 (SHAP)</h6>
    <p className="hint">실제 SOS 이상 점수에 대한 선택적 SHAP 분석입니다.
      계산은 요청할 때만 수행하며 기존 점수 민감도와는 다른 설명 방법입니다.</p>
    {lookup === "loading" && <p role="status">이 후보의 기존 SHAP 결과를 찾고 있습니다.</p>}
    {lookup === "error" && <div role="alert">
      <p>이전 SHAP 작업을 확인하지 못했습니다.</p>
      <button type="button" className="secondary" onClick={() => setLookupRetry(n => n + 1)}>
        작업 이력 다시 조회</button>
    </div>}
    {canRun && (!job || terminal.has(job.status)) && <button type="button"
      disabled={busy || lookup !== "ready"} onClick={() => void submit()}>
      {busy ? "SHAP 분석 요청 중…" : job ? "SHAP 다시 계산 →" : "SHAP 기여도 계산 →"}
    </button>}
    {!canRun && <p className="hint">현재 계정은 설명 Job을 제출할 권한이 없습니다.</p>}
    {job && <p role="status">SHAP 작업: {job.status}
      {job.cancel_requested ? " · 취소 요청됨" : ""}{job.error_code ? " · 오류 " + job.error_code : ""}
    </p>}
    {job && !terminal.has(job.status) && canCancel && <button type="button"
      className="secondary" disabled={busy || job.cancel_requested} onClick={() => void cancel()}>
      {job.cancel_requested ? "취소 처리 중" : "SHAP 계산 취소 요청"}
    </button>}
    {error && <div role="alert">{error} <button type="button" className="secondary"
      onClick={() => { setError(""); setRetry(n => n + 1); }}>결과 다시 조회</button></div>}
    {job?.status === "Failed" && <p role="alert">SHAP 계산이 실패했습니다. 작업 이력에서 오류를 확인하세요.</p>}
    {job?.status === "Cancelled" && <p>SHAP 계산이 취소됐습니다.</p>}
    {result && <><ShapContributionChart result={result} />
      <button type="button" className="secondary"
        onClick={() => onOpenResource("explanation", result.id)}>SHAP 결과 판본 보기 →</button>
    </>}
  </div>;
}
