/** 기존 독립 설명 Job을 SHAP 방법으로 요청하고 해당 결과를 탐구한다. */
import { useEffect, useMemo, useRef, useState } from "react";
import { CHART_COLORS, DataChart } from "./DataChart";
import { EvidenceFacts } from "./EvidenceFacts";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { formatError, request, submitJob } from "./api";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ExplanationResult, MLExplanationJob, MLExplanationJobSummary } from "./types";

/** ECharts 기본 HTML 툴팁에 Dataset/모델 Feature 이름을 직접 삽입하지 않는다. */
export function escapeShapTooltipText(text: string): string {
  const entities: Record<string, string> = {
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  };
  return text.replace(/[&<>"']/g, char => entities[char]);
}

const METHOD = "permutation_shap_median_v1";
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
type JobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };
type Contribution = { feature: string; shap_value: number; actual_value: number | null;
  reference_median: number; missing_imputed: boolean };

export function ShapContributionChart({ result, valueLabel = "실제 관측값", inputLabel = "이 관측값" }: {
  result: ExplanationResult; valueLabel?: string; inputLabel?: string;
}) {
  // Explanation 판본별로 선택을 묶어 다른 관측값의 Feature 선택이 남지 않게 한다.
  const [featureSelection, setFeatureSelection] = useState<{
    resultId: string; feature: string;
  } | null>(null);
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
  // 선택 항목만 바뀌어도 ECharts를 재초기화하지 않도록 불변 설명 출력별로 차트 데이터를 보존한다.
  const { contributions, shown, rest, restSum, bars } = useMemo(() => {
    const contributions = [...values].sort((a, b) =>
      Math.abs(b.shap_value) - Math.abs(a.shap_value));
    const shown = contributions.slice(0, 8);
    const rest = contributions.slice(8);
    const restSum = rest.reduce((sum, item) => sum + item.shap_value, 0);
    const bars = [...shown].reverse().map(item => ({
      label: activityFeatureLabel(item.feature), feature: item.feature, value: item.shap_value,
    }));
    return { contributions, shown, rest, restSum, bars };
  }, [output]);
  const key = (value: unknown) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 7 }) : "미정의";
  const selected = (featureSelection && featureSelection.resultId === result.id
    ? shown.find(item => item.feature === featureSelection.feature) : undefined) ?? shown[0];
  const option = useMemo(() => ({
    baseOption: {
      aria: { enabled: true },
      tooltip: { trigger: "axis", formatter: (params: Array<{ dataIndex: number }>) => {
        const bar = bars[params[0]?.dataIndex ?? -1];
        if (!bar) return "";
        const name = bar.feature && bar.feature !== bar.label ? `${bar.label} (${bar.feature})` : bar.label;
        return `${escapeShapTooltipText(name)}<br/>점수 기여 ${key(bar.value)}`;
      } },
      grid: { left: 240, right: 45, top: 20, bottom: 44 },
      xAxis: { type: "value" as const, name: "점수 기여", nameGap: 24,
        axisLabel: { formatter: (value: number) => value.toFixed(3) } },
      yAxis: { type: "category" as const,
        data: bars.map(bar => bar.label),
        axisLabel: { width: 210, overflow: "truncate" as const, fontSize: 12 } },
      series: [{ type: "bar" as const, name: "기준 점수 대비 기여",
        data: bars.map(bar => bar.value),
        selectedMode: "single" as const,
        select: { label: { show: true, position: "right" as const, formatter: "●" },
          itemStyle: { borderWidth: 2 } },
        itemStyle: { color: (params: { value: number }) =>
          params.value >= 0 ? CHART_COLORS.positive : CHART_COLORS.negative },
        markLine: { silent: true, symbol: "none", label: { show: false },
          data: [{ xAxis: 0 }] } }],
    },
    media: [{ query: { maxWidth: 620 }, option: {
      grid: { left: 153, right: 20, top: 20, bottom: 52 },
      yAxis: { axisLabel: { width: 127, overflow: "truncate" as const, fontSize: 11 } },
    } }],
  }), [bars]);
  const selectedBarIndex = bars.findIndex(bar => bar.feature === selected?.feature);
  // 학습 중앙값 기준 입력을 그대로 설명하면 유효한 SHAP 결과도 모두 0이다.
  // 설명의 근거를 숨기지 않되 값이 없는 차트를 일반 기여도 순위처럼 보여주지 않는다.
  const zeroContributions = isValid && values.every(item => item.shap_value === 0);
  if (!isValid) {
    return <p role="alert">SHAP 설명 근거의 점수·기여도 검증에 실패했습니다.
      이 결과의 차트는 표시하지 않습니다. 설명 결과 판본을 확인하세요.</p>;
  }
  return <section aria-label="SHAP 모델 점수 기여도" className="pipeline-shap-result">
    <h6>{zeroContributions ? "SHAP 기준 입력과 동일한 결과" :
      `모델 점수에 기여한 주요 Feature ${shown.length}개 (SHAP)`}</h6>
    {zeroContributions
      ? <div className="pipeline-shap-zero" role="status">
          <strong>이 입력의 SHAP 기여도는 모두 0입니다.</strong>
          <p>이 입력은 설명에 사용한 학습 중앙값 기준 입력과 같은 점수를 냅니다.
            기준 점수와 실제 점수는 {key(score)}로 일치합니다.</p>
          <p>다른 입력이나 관측값을 점수화하면 그 결과의 기여도를 살펴볼 수 있습니다.
            입력을 바꾼다고 반드시 점수가 달라지는 것은 아닙니다.</p>
        </div>
      : <>
          <p className="pipeline-shap-summary">기준 점수 <strong>{key(baseline)}</strong> +
            Feature 기여도 합 <strong>{key(shapSum)}</strong> =
            실제 이상 점수 <strong>{key(score)}</strong></p>
          <p className="pipeline-shap-legend"><span className="pipeline-shap-positive">양수: 점수를 높이는 방향</span>
            <span className="pipeline-shap-negative">음수: 점수를 낮추는 방향</span></p>
          <DataChart option={option} selectedDataIndex={selectedBarIndex}
            ariaLabel={`실제 모델 이상 점수 기준 주요 ${shown.length}개 Feature의 개별 양수·음수 Permutation SHAP 기여도. 현재 살펴보는 Feature: ${activityFeatureLabel(selected.feature)}`}
            onDataClick={index => {
              const entry = bars[index];
              if (entry) setFeatureSelection({ resultId: result.id, feature: entry.feature });
            }} />
          <div className="pipeline-shap-feature-focus" aria-label="선택한 SHAP Feature 근거">
            <label className="pipeline-shap-feature-picker">
              <span>Feature 살펴보기</span>
              <select value={selected.feature} onChange={event =>
                setFeatureSelection({ resultId: result.id, feature: event.target.value })}>
                {shown.map(item => <option key={item.feature} value={item.feature}>
                  {activityFeatureLabel(item.feature)}
                </option>)}
              </select>
            </label>
            <div className="pipeline-shap-feature-values" aria-live="polite">
              <div><span>{valueLabel}</span>
                <strong>{selected.missing_imputed ? "결측 대체" : key(selected.actual_value)}</strong></div>
              <div><span>학습 참조 중앙값</span><strong>{key(selected.reference_median)}</strong></div>
              <div><span>SHAP 점수 기여</span><strong>{key(selected.shap_value)}</strong></div>
            </div>
            <p className="hint">차트 막대 또는 목록에서 Feature를 선택하면 {inputLabel}의 값과 점수 기여를 비교할 수 있습니다.</p>
          </div>
          {rest.length > 0 && <div className="pipeline-shap-remainder">
            <span>나머지 {rest.length}개 Feature 기여도 합계</span>
            <strong>{key(restSum)}</strong>
            <small>전체 기여도 합에 포함되며 단일 Feature의 기여도가 아닙니다.</small>
          </div>}
        </>}
    <p className="hint">저장된 모델 점수와 SHAP 설명의 재구성 오차 {key(residual)}를 확인했습니다.
      기여도는 사고의 원인이나 확률을 의미하지 않습니다.</p>
    <details className="pipeline-shap-technical">
      <summary>전체 SHAP 값과 계산 근거</summary>
      <div className="pipeline-shap-technical-content">
        <section aria-label="SHAP 설명 방법과 기준">
          <h6>설명 방법과 기준</h6>
          <EvidenceFacts facts={[
            { label: "설명 방법", value: "Permutation SHAP" },
            { label: "모델 점수 출력", value: "-score_samples" },
            { label: "기준 배경", value: `합성 관측값 ${key(output.baseline_row_count)}개` },
          ]} />
          <p className="hint">Train Feature 중앙값으로 구성한 합성 관측값을 기준으로 합니다.
            실제 Train 관측 분포를 대표하지 않습니다.</p>
        </section>
        <section aria-label="SHAP 재현 설정">
          <h6>재현 설정</h6>
          <EvidenceFacts compact facts={[
            { label: "순열 반복", value: typeof output.permutation_rounds === "number"
              ? `${key(output.permutation_rounds)}회` : "미정의" },
            { label: "Seed", value: key(output.sampling_seed) },
            { label: "SHAP 라이브러리", value: String(output.shap_version ?? "미정의") },
          ]} />
        </section>
      </div>
      <div className="table-scroll"><table>
        <thead><tr><th>Feature</th><th>관측값</th><th>Train 중앙값</th><th>점수 기여</th></tr></thead>
        <tbody>{contributions.map(item => <tr key={item.feature}>
          <th>{activityFeatureLabel(item.feature)}
            {activityFeatureLabel(item.feature) !== item.feature &&
              <small className="activity-feature-original">{item.feature}</small>}</th><td>{item.missing_imputed ? "결측 대체" : key(item.actual_value)}</td>
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
    <p className="hint">실제 모델 이상 점수에 대한 선택적 SHAP 분석입니다.
      계산은 요청할 때만 수행하며 기존 점수 민감도와는 다른 설명 방법입니다.</p>
    {lookup === "loading" && <p role="status">이 후보의 기존 SHAP 결과를 찾고 있습니다.</p>}
    {lookup === "error" && <div role="alert">
      <p>이전 SHAP 작업을 확인하지 못했습니다.</p>
      <button type="button" className="secondary" onClick={() => setLookupRetry(n => n + 1)}>
        작업 이력 다시 조회</button>
    </div>}
    {canRun && !result && (!job || terminal.has(job.status)) &&
      <button type="button" disabled={busy || lookup !== "ready"} onClick={() => void submit()}>
        {busy ? "SHAP 분석 요청 중…" :
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
      {canRun && <button type="button" className="secondary pipeline-shap-recalculate"
        disabled={busy || lookup !== "ready"} onClick={() => void submit()}>
        {busy ? "SHAP 분석 요청 중…" : "SHAP 다시 계산"}
      </button>}
    </>}
  </div>;
}
