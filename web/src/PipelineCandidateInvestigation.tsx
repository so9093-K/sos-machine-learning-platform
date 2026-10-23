/** Pipeline 후보 한 건의 관측값과 명시적으로 계산한 설명 결과를 분석한다. */
import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { DataChart } from "./DataChart";
import type { MLResourceKind } from "./PlatformNavigation";
import type { MLExplanationJob, MLExplanationJobSummary, ExplanationResult, PipelineCandidates } from "./types";

type Candidate = PipelineCandidates["items"][number];
type ExplanationJobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };

export function matchingExplanationJob(
  jobs: MLExplanationJobSummary[], scoringResultId: string, rowIndex: number,
): MLExplanationJobSummary | undefined {
  return jobs.filter(item => item.spec.scoring_result_id === scoringResultId &&
    item.spec.row_index === rowIndex)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function format(value: unknown): string {
  const num = numeric(value);
  return num === null ? "미정의" :
    num.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
}

export function SensitivityChart({ result }: { result: ExplanationResult }) {
  const raw = result.output.feature_sensitivities;
  const entries = Array.isArray(raw) ? raw as Array<Record<string, unknown>> : [];
  const ranked = entries.filter(entry => typeof entry.feature === "string" &&
    numeric(entry.anomaly_score_delta) !== null)
    .sort((a, b) => Math.abs(numeric(b.anomaly_score_delta)!) -
      Math.abs(numeric(a.anomaly_score_delta)!)).slice(0, 12);
  if (!ranked.length) return <p>시각화 가능한 점수 민감도 근거가 없습니다.</p>;
  const option = {
    aria: { enabled: true },
    tooltip: { trigger: "axis" },
    grid: { left: "24%", right: "8%", top: 18, bottom: 32 },
    xAxis: { type: "value" as const, name: "점수 변화" },
    yAxis: { type: "category" as const,
      data: ranked.map(x => String(x.feature)).reverse(),
      axisLabel: { width: 120, overflow: "truncate" as const } },
    series: [{ type: "bar" as const, name: "기준값 치환 시 점수 변화",
      data: ranked.map(x => numeric(x.anomaly_score_delta)).reverse() }],
  };
  return <>
    <DataChart option={option} ariaLabel="선택한 후보의 Feature별 참조 중앙값 치환에 따른 이상 점수 민감도" />
    <p className="hint">실제 모델에서 각 Feature를 Train 참조 중앙값으로 하나씩 치환했을 때의 점수 차이입니다.
      SHAP·가법 기여도·원인 판정이 아닙니다.</p>
  </>;
}

export function PipelineCandidateInvestigation({ candidate, scoringResultId, canRun, onOpenResource }: {
  candidate: Candidate; scoringResultId: string; canRun: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<ExplanationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [lookupRetry, setLookupRetry] = useState(0);
  const [previousLookup, setPreviousLookup] = useState<"loading" | "ready" | "error">("loading");
  const submitted = useRef(false);
  useEffect(() => {
    let active = true;
    setPreviousLookup("loading");
    // 기존 권한 계약을 사용하는 최근 Job 목록에서 동일 후보의 설명 기록을 복원한다.
    void request<ExplanationJobPage>("/api/ml/explanations/jobs?limit=100")
      .then(page => {
        if (!active || submitted.current) return;
        const previous = matchingExplanationJob(page.items, scoringResultId, candidate.row_index);
        if (!previous) {
          setPreviousLookup("ready");
          return;
        }
        return request<MLExplanationJob>(
          "/api/ml/explanations/jobs/" + encodeURIComponent(previous.id)
        ).then(restored => {
          if (!active || submitted.current) return;
          if (restored.spec.scoring_result_id !== scoringResultId ||
              restored.spec.row_index !== candidate.row_index) {
            throw new Error("이전 설명 Job의 Scoring Result와 후보 행 번호가 일치하지 않습니다.");
          }
          setJob(restored); setPreviousLookup("ready");
        });
      })
      .catch(() => { if (active && !submitted.current) setPreviousLookup("error"); });
    return () => { active = false; };
  }, [scoringResultId, candidate.row_index, lookupRetry]);
  useEffect(() => {
    if (!job || terminal.has(job.status) || error) return;
    let active = true;
    const interval = setInterval(() => {
      void request<MLExplanationJob>("/api/ml/explanations/jobs/" + encodeURIComponent(job.id))
        .then(next => { if (active) { setJob(next); setError(""); } })
        .catch(e => { if (active) setError(formatError(e)); });
    }, 2000);
    return () => { active = false; clearInterval(interval); };
  }, [job?.id, job?.status, error, retry]);
  useEffect(() => {
    const resultId = job?.result?.explanation_result_id;
    if (job?.status !== "Succeeded") return;
    if (typeof resultId !== "string") {
      setError("완료된 설명 Job에서 설명 결과 판본을 찾지 못했습니다.");
      return;
    }
    let active = true;
    void request<ExplanationResult>("/api/ml/explanations/" + encodeURIComponent(resultId))
      .then(next => {
        if (!active) return;
        if (next.scoring_result_id !== scoringResultId ||
            next.row_index !== candidate.row_index) {
          setError("설명 판본과 선택한 후보의 Scoring 행 번호가 일치하지 않습니다.");
          return;
        }
        setResult(next); setError("");
      })
      .catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, [job?.id, job?.status, job?.result?.explanation_result_id, scoringResultId, candidate.row_index, retry]);
  async function start() {
    if (!canRun || busy || previousLookup !== "ready" ||
        (job && !terminal.has(job.status))) return;
    submitted.current = true;
    setBusy(true); setJob(null); setResult(null); setError("");
    try {
      setJob(await submitJob<MLExplanationJob>("/api/ml/explanations/jobs", {
        scoring_result_id: scoringResultId, row_index: candidate.row_index,
      }));
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  return <section className="pipeline-candidate-inspection" aria-label="선택한 후보 분석">
    <h5 id="pipeline-candidate-detail-heading" tabIndex={-1}>
      선택한 후보 #{candidate.row_index.toLocaleString("ko-KR")}</h5>
    <p>이상 점수 <strong>{format(candidate.anomaly_score)}</strong> · Test Scoring 내부 행 번호</p>
    <details><summary>이 후보의 Feature 값 확인</summary>
      <div className="table-scroll"><table>
        <thead><tr><th>Feature</th><th>관측값</th></tr></thead>
        <tbody>{Object.entries(candidate.features).map(([name, value]) =>
          <tr key={name}><th>{name}</th><td>{format(value)}</td></tr>)}</tbody>
      </table></div>
    </details>
    {previousLookup === "loading" && <p role="status">
      이 후보의 기존 설명 작업을 확인하고 있습니다.
    </p>}
    {previousLookup === "error" && <div className="pipeline-readiness-blocked" role="alert">
      <p>이전 설명 작업을 확인하지 못했습니다. 중복 실행을 방지하도록 작업 이력을 다시 확인하세요.</p>
      <button type="button" className="secondary" onClick={() =>
        setLookupRetry(n => n + 1)}>이전 설명 작업 다시 조회</button>
    </div>}
    {(!job || job.status === "Failed" || job.status === "Cancelled") && canRun &&
      <button type="button" onClick={() => void start()}
        disabled={busy || previousLookup !== "ready"}>
        {busy ? "설명 Job 제출 중…" : job ? "새 설명 Job으로 다시 계산 →" :
          "이 후보의 점수 민감도 계산 →"}
      </button>}
    {job && previousLookup === "ready" && <p className="hint">
      이 후보에 연결된 설명 Job을 조회했습니다. 다른 후보를 살펴본 뒤에도 다시 확인할 수 있습니다.
    </p>}
    {!canRun && <p className="hint">현재 계정은 설명 Job을 제출할 권한이 없습니다.</p>}
    {job && <p role="status">설명 Job: {job.status}
      {job.status === "Running" || job.status === "Queued" ? " · 계산 중" : ""}
      {job.error_code ? " · 오류 " + job.error_code : ""}</p>}
    {job?.status === "Failed" && <p role="alert">설명 Job이 실패했습니다. 실행 이력에서 오류 근거를 확인하세요.</p>}
    {job?.status === "Cancelled" && <p>설명 계산이 취소됐습니다.</p>}
    {error && <div role="alert" className="pipeline-readiness-blocked">{error}
      {job && <button className="secondary" type="button" onClick={() => {
        setError(""); setRetry(n => n + 1);
      }}>설명 근거 다시 조회하기</button>}</div>}
    {result && <>
      <SensitivityChart result={result} />
      <button type="button" className="secondary" onClick={() =>
        onOpenResource("explanation", result.id)}>설명 결과 판본 보기 →</button>
    </>}
  </section>;
}
