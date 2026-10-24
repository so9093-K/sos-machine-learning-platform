/** 기존 독립 설명 Job을 SHAP 방법으로 요청하고 해당 결과를 탐구한다. */
import { useEffect, useRef, useState } from "react";
import { CHART_COLORS, DataChart } from "./DataChart";
import { formatError, request, submitJob } from "./api";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ExplanationResult, MLExplanationJob, MLExplanationJobSummary } from "./types";

const METHOD = "permutation_shap_median_v1";
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
type JobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };
type Contribution = { feature: string; shap_value: number; actual_value: number | null;
  reference_median: number; missing_imputed: boolean };

export function ShapContributionChart({ result }: { result: ExplanationResult }) {
  const output = result.output;
  const values = Array.isArray(output.feature_contributions)
    ? output.feature_contributions as Contribution[] : [];
  const numeric = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);
  const baseline = output.baseline_score;
  const score = output.score;
  const reconstructed = output.reconstructed_score;
  const residual = output.additivity_residual;
  const allValid = values.length > 0 && values.every(item =>
    typeof item.feature === "string" && item.feature.length > 0 &&
    numeric(item.shap_value) && numeric(item.reference_median) &&
    (item.actual_value === null || numeric(item.actual_value)));
  const shapSum = allValid ? values.reduce((sum, item) => sum + item.shap_value, 0) : NaN;
  const tolerance = 1e-7;
  const isValid = output.method === METHOD && output.additive_attribution === true &&
    output.model_output === "negative_sklearn_score_samples" &&
    output.baseline_row_count === 1 && allValid &&
    numeric(baseline) && numeric(score) && numeric(reconstructed) && numeric(residual) &&
    Math.abs(baseline + shapSum - score) <= tolerance &&
    Math.abs(reconstructed - score) <= tolerance &&
    Math.abs(residual - (score - reconstructed)) <= tolerance;
  if (!isValid) {
    return <p role="alert">SHAP 설명 근거의 점수·기여도 검증에 실패했습니다.
      이 결과의 차트는 표시하지 않습니다. 설명 결과 판본을 확인하세요.</p>;
  }
  const contributions = [...values].sort((a, b) =>
    Math.abs(b.shap_value) - Math.abs(a.shap_value));
  const key = (value: unknown) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 7 }) : "미정의";
  const top = contributions.slice(0, 8).reverse();
  const option = {
    baseOption: {
      aria: { enabled: true },
      tooltip: { trigger: "axis" },
      grid: { left: 240, right: 45, top: 20, bottom: 44 },
      xAxis: { type: "value" as const, name: "점수 기여", nameGap: 24,
        axisLabel: { formatter: (value: number) => value.toFixed(3) } },
      yAxis: { type: "category" as const,
        data: top.map(x => x.feature),
        axisLabel: { width: 210, overflow: "truncate" as const, fontSize: 12 } },
      series: [{ type: "bar" as const, name: "기준 점수 대비 기여",
        data: top.map(x => x.shap_value),
        itemStyle: { color: (params: { value: number }) =>
          params.value >= 0 ? CHART_COLORS.positive : CHART_COLORS.negative },
        markLine: { silent: true, symbol: "none", label: { show: false },
          data: [{ xAxis: 0 }] } }],
    },
    media: [{ query: { maxWidth: 620 }, option: {
      grid: { left: 153, right: 20, top: 20, bottom: 52 },
      yAxis: { axisLabel: { width: 127, overflow: "truncate" as const, fontSize: 11 } },
    } }],
  };
  return <section aria-label="SHAP 모델 점수 기여도" className="pipeline-shap-result">
    <h6>모델 이상 점수에 대한 SHAP 기여도</h6>
    <p>기준 점수 <strong>{key(baseline)}</strong> +
      Feature 기여도 합 <strong>{key(shapSum)}</strong> =
      이상 점수 <strong>{key(score)}</strong></p>
    <p className="hint">계산 결과의 재구성 오차 {key(residual)} ·
      저장된 모델 점수와 가법적 설명 결과를 검증했습니다.</p>
    <p className="pipeline-shap-legend"><span className="pipeline-shap-positive">양수: 이상 점수를 높이는 방향</span>
      <span className="pipeline-shap-negative">음수: 이상 점수를 낮추는 방향</span></p>
    <DataChart option={option} ariaLabel="실제 SOS 이상 점수 기준 주요 8개 Feature의 양수·음수 Permutation SHAP 기여도" />
    <p className="hint">Train Feature 중앙값으로 구성한 합성 관측치 한 행을 기준으로 합니다.
      실제 이상 사건의 원인이나 확률을 의미하지 않습니다.</p>
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

export function PipelineShapExplanation({
  candidate, scoringResultId, modelRevisionId, canRun, canCancel, onOpenResource,
}: {
  candidate: { row_index: number; anomaly_score: number };
  scoringResultId: string; modelRevisionId: string | null;
  canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<ExplanationResult | null>(null);
  const [lookup, setLookup] = useState<"loading" | "ready" | "error">("loading");
  const [lookupRetry, setLookupRetry] = useState(0);
  const [olderJobsExist, setOlderJobsExist] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [resultLoading, setResultLoading] = useState(false);
  const submitted = useRef(false);

  useEffect(() => {
    let active = true;
    setLookup("loading");
    void request<JobPage>("/api/ml/explanations/jobs?limit=100")
      .then(page => {
        if (!active || submitted.current) return;
        const prior = page.items.filter(item =>
          item.spec.scoring_result_id === scoringResultId &&
          item.spec.row_index === candidate.row_index &&
          item.spec.method === METHOD
        ).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
        if (!prior) { setOlderJobsExist(Boolean(page.next_cursor)); setLookup("ready"); return; }
        setOlderJobsExist(false);
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
    setResultLoading(true);
    void request<ExplanationResult>("/api/ml/explanations/" + encodeURIComponent(id))
      .then(next => {
        if (!active) return;
        const explainedScore = next.output.score;
        if (next.scoring_result_id !== scoringResultId ||
            next.row_index !== candidate.row_index || next.method !== METHOD ||
            (modelRevisionId !== null && next.model_revision_id !== modelRevisionId) ||
            typeof explainedScore !== "number" || !Number.isFinite(explainedScore) ||
            Math.abs(explainedScore - candidate.anomaly_score) >
              1e-10 + 1e-9 * Math.abs(candidate.anomaly_score)) {
          setResult(null);
          setError("SHAP 설명 판본의 모델·관측치·이상 점수가 현재 후보와 일치하지 않습니다.");
        } else { setResult(next); setError(""); }
      }).catch(reason => { if (active) setError(formatError(reason)); })
      .finally(() => { if (active) setResultLoading(false); });
    return () => { active = false; };
  }, [job?.id, job?.status, job?.result?.explanation_result_id,
    scoringResultId, candidate.row_index, candidate.anomaly_score, modelRevisionId, retry]);

  async function submit() {
    if (!canRun || busy || lookup !== "ready" || (job && !terminal.has(job.status))) return;
    submitted.current = true;
    setBusy(true); setJob(null); setResult(null); setError("");
    try {
      setJob(await submitJob<MLExplanationJob>("/api/ml/explanations/jobs", {
        scoring_result_id: scoringResultId, row_index: candidate.row_index, method: METHOD,
      }));
    } catch (e) {
      submitted.current = false;
      setError("SHAP 계산 요청에 실패했습니다: " + formatError(e));
    }
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
    {canRun && (!job || terminal.has(job.status)) &&
      <button type="button" className={result ? "secondary pipeline-shap-recalculate" : ""}
        disabled={busy || lookup !== "ready"} onClick={() => void submit()}>
        {busy ? "SHAP 분석 요청 중…" : result ? "SHAP 다시 계산" :
          job ? "SHAP 다시 계산 →" : "SHAP 기여도 계산 →"}
      </button>}
    {!job && lookup === "ready" && olderJobsExist && <p className="hint">
      최근 100건에 기존 SHAP 작업이 없습니다. 오래된 결과는 독립 설명 작업 이력에서 확인하세요.
    </p>}
    {!canRun && <p className="hint">현재 계정은 설명 Job을 제출할 권한이 없습니다.</p>}
    {job && <p role="status" className="pipeline-shap-job-status">
      {job.status === "Succeeded" ? "SHAP 분석 완료" :
        job.status === "Running" ? "SHAP 분석 중" :
        job.status === "Queued" ? "SHAP 분석 대기 중" :
        job.status === "Cancelled" ? "SHAP 분석 취소됨" : "SHAP 분석 실패"}
      {job.cancel_requested ? " · 취소 요청됨" : ""}
      {job.error_code ? " · 오류 " + job.error_code : ""}
    </p>}
    {job && !terminal.has(job.status) && canCancel && <button type="button"
      className="secondary" disabled={busy || job.cancel_requested} onClick={() => void cancel()}>
      {job.cancel_requested ? "취소 처리 중" : "SHAP 계산 취소 요청"}
    </button>}
    {job?.status === "Succeeded" && !result && !error && resultLoading &&
      <p role="status">SHAP 계산이 완료됐습니다. 결과 판본을 불러오는 중입니다.</p>}
    {error && <div role="alert">
      <p>{error}</p>
      {job && <button type="button" className="secondary"
        onClick={() => { setError(""); setRetry(n => n + 1); }}>설명 결과 다시 조회</button>}
      {!job && <p className="hint">위의 ‘SHAP 기여도 계산’으로 다시 요청할 수 있습니다.</p>}
    </div>}
    {job?.status === "Failed" && <p role="alert">SHAP 계산이 실패했습니다. 작업 이력에서 오류를 확인하세요.</p>}
    {job?.status === "Cancelled" && <p>SHAP 계산이 취소됐습니다.</p>}
    {result && <><ShapContributionChart result={result} />
      <button type="button" className="secondary"
        onClick={() => onOpenResource("explanation", result.id)}>SHAP 결과 판본 보기 →</button>
    </>}
  </div>;
}
