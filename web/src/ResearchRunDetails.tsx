import { useState } from "react";
import { formatError, request } from "./api";
import { jobStatusLabel } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Job, MLflowRun } from "./types";

const operations: Record<string, { endpoint: string; resource?: MLResourceKind; field: string }> = {
  ml_train: { endpoint: "train", resource: "model", field: "model_revision_id" },
  ml_score: { endpoint: "scoring", resource: "scoring", field: "scoring_result_id" },
  ml_policy: { endpoint: "policies", resource: "policy", field: "policy_revision_id" },
  ml_evaluate: { endpoint: "evaluations", resource: "evaluation", field: "evaluation_revision_id" },
  ml_explain: { endpoint: "explanations", resource: "explanation", field: "explanation_result_id" },
  ml_playground: { endpoint: "playground", field: "" },
};
/** MLflow 기록을 실행 상태의 정본으로 사용하지 않고 해당 SOS Job을 명시적으로 조회한다. */
export function ResearchRunDetails({ run, onOpenResource }: {
  run: MLflowRun; onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [detail, setDetail] = useState<Job | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const operation = operations[run.job_kind];
  if (!operation) return null;
  async function load() {
    if (loading) return;
    setLoading(true); setError("");
    try {
      const job = await request<Job>(`/api/ml/${operation.endpoint}/jobs/${encodeURIComponent(run.job_id)}`);
      if (job.id !== run.job_id || job.kind !== run.job_kind) throw new Error("연구 Run과 연결된 작업이 일치하지 않습니다.");
      setDetail(job);
    } catch (cause) { setError(formatError(cause)); }
    finally { setLoading(false); }
  }
  const resultId = detail?.status === "Succeeded" ? detail.result?.[operation.field] : null;
  return <details className="research-run-detail" onToggle={event => {
    if (event.currentTarget.open && !detail && !loading && !error) void load();
  }}>
    <summary>SOS 실행 상세·결과</summary>
    {loading && <p role="status">실행 상태 조회 중…</p>}
    {error && <p role="alert">{error}</p>}
    {detail && <>
      <p role="status">마지막 확인 상태 · {jobStatusLabel(detail.status)}</p>
      {detail.error && <p role="alert">{detail.error}</p>}
      {typeof resultId === "string" && operation.resource && <button type="button" className="secondary"
        onClick={() => onOpenResource(operation.resource!, resultId)}>이 실행의 결과 조회 →</button>}
      {run.job_kind === "ml_playground" && typeof detail.result?.anomaly_score === "number" &&
        <p>이상 점수 · {detail.result.anomaly_score.toFixed(6)}</p>}
      <details><summary>제출 조건·기술 근거</summary>
        <pre>{JSON.stringify({ job_id: run.job_id, error_code: detail.error_code, spec: detail.spec, result: detail.result }, null, 2)}</pre>
      </details>
    </>}
    {(detail || error) && <button type="button" className="secondary" disabled={loading} onClick={() => void load()}>실행 상태 다시 조회</button>}
  </details>;
}
