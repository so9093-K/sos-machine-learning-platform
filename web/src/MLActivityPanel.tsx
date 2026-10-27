import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";
import type {
  MLTrainingJobSummary, MLScoringJobSummary, MLPolicyJobSummary,
  MLEvaluationJobSummary, MLExplanationJobSummary,
} from "./types";
import { jobStatusLabel } from "./display-format";

type Kind = "training" | "scoring" | "policy" | "evaluation" | "explanation";
type JobLike = {
  id: string;
  status: string;
  created_at: string;
  result?: object | null;
  error_code?: string | null;
  cancel_requested?: boolean;
};
const operations: Record<Kind, {
  label: string; tab: WorkspaceTab; resource: MLResourceKind; field: string; endpoint: string;
}> = {
  training: { label: "모델 학습", tab: "training", resource: "model", field: "model_revision_id", endpoint: "/api/ml/train/jobs" },
  scoring: { label: "점수화", tab: "scoring", resource: "scoring", field: "scoring_result_id", endpoint: "/api/ml/scoring/jobs" },
  policy: { label: "판정 정책", tab: "policies", resource: "policy", field: "policy_revision_id", endpoint: "/api/ml/policies/jobs" },
  evaluation: { label: "평가", tab: "evaluations", resource: "evaluation", field: "evaluation_revision_id", endpoint: "/api/ml/evaluations/jobs" },
  explanation: { label: "설명", tab: "explanations", resource: "explanation", field: "explanation_result_id", endpoint: "/api/ml/explanations/jobs" },
};
type Activity = { id: string; kind: Kind; status: string; created: string;
  resultId: string | null; error: string | null; cancelRequested: boolean };
function toActivity(job: JobLike, kind: Kind): Activity {
  const result = job.result as Record<string, unknown> | null | undefined;
  const value = result?.[operations[kind].field];
  return { id: job.id, kind, status: job.status, created: job.created_at,
    resultId: typeof value === "string" ? value : null,
    error: job.error_code ?? null, cancelRequested: job.cancel_requested === true };
}

type JobDetail = {
  status: string;
  cancel_requested?: boolean;
  error?: string | null;
  error_code?: string | null;
  spec?: unknown;
  attempts?: unknown;
};
function ActivityJobDetails({ job, canCancel, onChanged }: {
  job: Activity;
  canCancel: boolean;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function loadDetails() {
    if (loading) return;
    setLoading(true);
    setError("");
    try {
      const result = await request<JobDetail>(
        `${operations[job.kind].endpoint}/${encodeURIComponent(job.id)}`);
      setDetail(result);
    } catch (e) { setError(formatError(e)); }
    finally { setLoading(false); }
  }
  async function cancel() {
    if (!canCancel || !detail || !["Queued", "Running"].includes(detail.status) ||
      detail.cancel_requested || cancelling) return;
    setCancelling(true);
    setError("");
    try {
      const updated = await request<JobDetail>(
        `${operations[job.kind].endpoint}/${encodeURIComponent(job.id)}/cancel`,
        { method: "POST" },
      );
      setDetail(updated);
      setNotice("취소 요청이 접수됐습니다. 실제 종료 상태는 다시 조회해 확인하세요.");
      onChanged();
    } catch (e) { setError(formatError(e)); }
    finally { setCancelling(false); }
  }
  return <details className="ml-activity-details" onToggle={event => {
    if (event.currentTarget.open && !detail && !loading && !error) void loadDetails();
  }}>
    <summary>Job 상태·설정·진단</summary>
    {loading && <p role="status">실행 상세 조회 중…</p>}
    {error && <div><p role="alert">{error}</p>
      <button className="secondary" onClick={() => void loadDetails()}>다시 조회</button></div>}
    {notice && <p role="status">{notice}</p>}
    {detail && <>
      <p className="hint">마지막 상세 조회 상태: {detail.status}
        {detail.cancel_requested ? " · 취소 요청 중" : ""}
        {detail.error_code ? ` · 오류 코드 ${detail.error_code}` : ""}</p>
      {detail.error && <p role="alert">{detail.error}</p>}
      <pre>{JSON.stringify({ spec: detail.spec ?? "제공되지 않음",
        attempts: detail.attempts ?? "상세 API에 실행 시도 정보 없음" }, null, 2)}</pre>
      <div className="ml-activity-actions">
        {canCancel && ["Queued", "Running"].includes(detail.status) &&
          !detail.cancel_requested && <button className="secondary"
            disabled={cancelling || loading} onClick={() => void cancel()}>
            {cancelling ? "취소 요청 중…" : "작업 취소 요청"}
          </button>}
        <button className="secondary" disabled={loading}
          onClick={() => void loadDetails()}>상태 다시 조회</button>
      </div>
    </>}
  </details>;
}

/** 조회된 Job 페이지에 있는 활동만 보여준다. 전체 Job 수나 완료 진행률을 추정하지 않는다. */
export function MLActivityPanel({ compact = false, canCancel = false, onNavigate, onOpenResource, onViewAll }: {
  compact?: boolean;
  canCancel?: boolean;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onViewAll?: () => void;
}) {
  const training = usePagedJobs<MLTrainingJobSummary>("/api/ml/train/jobs");
  const scoring = usePagedJobs<MLScoringJobSummary>("/api/ml/scoring/jobs");
  const policy = usePagedJobs<MLPolicyJobSummary>("/api/ml/policies/jobs");
  const evaluation = usePagedJobs<MLEvaluationJobSummary>("/api/ml/evaluations/jobs");
  const explanation = usePagedJobs<MLExplanationJobSummary>("/api/ml/explanations/jobs");
  const [filter, setFilter] = useState<Kind | "all">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sources = { training, scoring, policy, evaluation, explanation };
  async function refresh() {
    setLoading(true);
    try {
      const settled = await Promise.allSettled(Object.values(sources).map(source => source.refresh()));
      setError(settled.some(entry => entry.status === "rejected")
        ? "일부 ML 작업 목록을 조회하지 못했습니다. 결과가 누락되거나 이전 조회 상태일 수 있습니다." : "");
    } finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  const items = (Object.keys(operations) as Kind[])
    .flatMap(kind => sources[kind].items.map(job => toActivity(job, kind)))
    .filter(job => filter === "all" || filter === job.kind)
    .sort((a, b) => b.created.localeCompare(a.created) || b.id.localeCompare(a.id))
    .slice(0, compact ? 4 : undefined);
  return <section className="panel ml-activity" aria-labelledby={compact ? "recent-ml-heading" : "ml-activity-heading"}>
    <div className="panel-head"><div>
      <h3 id={compact ? "recent-ml-heading" : "ml-activity-heading"}>
        {compact ? "최근 ML 실행" : "ML 실행 이력"}
      </h3>
      <p>학습·점수화·정책·평가·설명 Job의 조회된 페이지 범위입니다.</p>
    </div>
      {compact
        ? <button className="secondary" onClick={onViewAll ?? (() => onNavigate("ml-jobs"))}>작업 이력 →</button>
        : <button className="secondary" disabled={loading} onClick={() => void refresh()}>
          {loading ? "조회 중…" : "다시 조회"}</button>}
    </div>
    {!compact && <label className="ml-activity-filter">작업 유형
      <select value={filter} onChange={event => setFilter(event.target.value as Kind | "all")}>
        <option value="all">모든 유형 (조회된 페이지)</option>
        {(Object.keys(operations) as Kind[]).map(kind =>
          <option value={kind} key={kind}>{operations[kind].label}</option>)}
      </select>
    </label>}
    {loading && <p role="status" className="hint">조회 가능한 ML 작업 이력을 확인하는 중입니다.</p>}
    {error && <p className="hint" role="alert">{error}</p>}
    <div className="ml-activity-list">
      {items.map(job => <article className="ml-activity-row" key={job.kind + ":" + job.id}>
        <div className="ml-activity-title">
          <strong>{operations[job.kind].label}</strong>
          <span className={`status ${job.status.toLowerCase()}`}>
            {jobStatusLabel(job.status)}{job.cancelRequested ? " · 취소 요청 중" : ""}
          </span>
        </div>
        <span className="ml-activity-meta">
          {new Date(job.created).toLocaleString("ko-KR")} · Job {job.id.slice(0, 12)}
          {job.error ? ` · 오류 코드 ${job.error}` : ""}
        </span>
        <div className="ml-activity-actions">
          {job.resultId && <button className="secondary"
            onClick={() => onOpenResource(operations[job.kind].resource, job.resultId!)}>
            결과 판본 보기 →
          </button>}
          <button className="secondary" onClick={() => onNavigate(operations[job.kind].tab)}>
            {job.resultId ? "관련 작업" : "실행 화면"} →
          </button>
        </div>
        {!compact && <ActivityJobDetails job={job} canCancel={canCancel}
          onChanged={() => void refresh()}/>}
      </article>)}
      {!loading && items.length === 0 && <p className="muted">
        {error ? "일부 이력을 조회하지 못해 표시할 작업이 없습니다." : "현재 조회된 ML Job이 없습니다."}
      </p>}
    </div>
    {!compact && filter !== "all" && sources[filter].nextCursor &&
      <button className="secondary" onClick={() =>
        void sources[filter].loadMore().catch(e => setError(formatError(e)))}>
        이전 {operations[filter].label} Job 더 보기
      </button>}
    {!compact && filter === "all" &&
      <p className="hint">더 오래된 Job을 찾으려면 작업 유형을 선택한 다음 이전 페이지를 조회하세요.</p>}
  </section>;
}
