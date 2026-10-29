import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { SensitivityChart } from "./FeatureSensitivity";
import { jobStatusLabel } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type { MLExplanationJob, MLExplanationJobSummary, ExplanationResult, PipelineCandidates } from "./types";
type ExplanationJobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);

export function matchingExplanationJob(
  jobs: MLExplanationJobSummary[], scoringResultId: string, rowIndex: number,
  method = "median_reference_feature_replacement",
): MLExplanationJobSummary | undefined {
  return jobs.filter(item => item.spec.scoring_result_id === scoringResultId &&
    item.spec.row_index === rowIndex &&
    (item.spec.method ?? "median_reference_feature_replacement") === method)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}
export function ObservationSensitivity({ candidate, scoringResultId, canRun, canCancel, onOpenResource }: {
  candidate: PipelineCandidates["items"][number]; scoringResultId: string; canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const subject = "관측값";
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<ExplanationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [lookupRetry, setLookupRetry] = useState(0);
  const [previousLookup, setPreviousLookup] = useState<"loading" | "ready" | "error">("loading");
  const [olderJobsExist, setOlderJobsExist] = useState(false);
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
          setOlderJobsExist(Boolean(page.next_cursor));
          setPreviousLookup("ready");
          return;
        }
        setOlderJobsExist(false);
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
  async function cancelExplanation() {
    if (!job || terminal.has(job.status) || job.cancel_requested || busy || !canCancel) return;
    const requestedJobId = job.id;
    setBusy(true); setError("");
    try {
      const updated = await request<MLExplanationJob>(
        "/api/ml/explanations/jobs/" + encodeURIComponent(requestedJobId) + "/cancel",
        { method: "POST" },
      );
      setJob(previous => previous?.id === requestedJobId ? updated : previous);
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  return <section className="sensitivity-panel" aria-labelledby={`sensitivity-${candidate.row_index}`}>
    <h6 id={`sensitivity-${candidate.row_index}`}>기준값 치환 결과
      <small>중앙값 치환 실험</small></h6>
    {previousLookup === "loading" && <p role="status">
      이 {subject}의 기존 민감도 계산을 확인하고 있습니다.
    </p>}
    {previousLookup === "error" && <div className="notice-blocked" role="alert">
      <p>이전 민감도 계산을 확인하지 못했습니다. 중복 실행을 막기 위해 다시 조회해 주세요.</p>
      <button type="button" className="secondary" onClick={() =>
        setLookupRetry(n => n + 1)}>이전 계산 다시 조회</button>
    </div>}
    {(!job || job.status === "Failed" || job.status === "Cancelled") && canRun &&
      <button type="button" className="secondary sensitivity-start" onClick={() => void start()}
        disabled={busy || previousLookup !== "ready"}>
        {busy ? "민감도 계산 요청 중…" : job ? "민감도 다시 계산 →" : "치환 민감도 계산 →"}
      </button>}
    {!job && previousLookup === "ready" && olderJobsExist && <p className="hint">
      최근 100건의 민감도 계산에서 같은 {subject}를 찾지 못했습니다.
      더 오래된 결과는 작업 이력의 설명 작업에서 확인할 수 있습니다.
    </p>}
    {job && previousLookup === "ready" && <p className="hint">
      이 {subject}에 대해 이전에 계산한 민감도입니다. 다른 {subject}를 살펴본 뒤에도 다시 확인할 수 있습니다.
    </p>}
    {!canRun && <p className="hint">현재 계정은 민감도 계산을 요청할 권한이 없습니다.</p>}
    {job && <p role="status">민감도 계산: {jobStatusLabel(job.status)}
      {job.cancel_requested ? " · 취소 요청됨 (Worker 확인 중)" :
        job.status === "Running" || job.status === "Queued" ? " · 계산 중" : ""}
      {job.error_code ? " · 오류 " + job.error_code : ""}</p>}
    {job && !terminal.has(job.status) && canCancel && <button
      type="button" className="secondary" disabled={busy || job.cancel_requested}
      onClick={() => void cancelExplanation()}>
      {job.cancel_requested ? "민감도 계산 취소 요청 처리 중" : "민감도 계산 취소 요청"}
    </button>}
    {job?.status === "Failed" && <p role="alert">민감도 계산이 실패했습니다. 작업 이력에서 오류 근거를 확인하세요.</p>}
    {job?.status === "Cancelled" && <p>민감도 계산이 취소됐습니다.</p>}
    {error && <div role="alert" className="notice-blocked">{error}
      {job && <button className="secondary" type="button" onClick={() => {
        setError(""); setRetry(n => n + 1);
      }}>설명 근거 다시 조회</button>}</div>}
    {result && <>
      <SensitivityChart result={result} />
      <button type="button" className="secondary" onClick={() =>
        onOpenResource("explanation", result.id)}>설명 결과 판본 보기 →</button>
    </>}
    </section>;
}
