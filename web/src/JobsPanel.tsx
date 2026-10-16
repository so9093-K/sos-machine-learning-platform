import { useRef, useState } from "react";
import { request, formatError } from "./api";
import type { Dataset, Job, JobSummary, ExplorationJob, ExplorationJobSummary } from "./types";

type AnyJobSummary = JobSummary | ExplorationJobSummary;

/** Discovery 전체 분석 결과는 일반 사용자에게 표로 우선 제공한다. */
function DiscoveryJobResult({ result }: { result: Record<string, unknown> }) {
  if (!Array.isArray(result.rows)) return null;
  const rows = result.rows as Array<{ category: unknown; value: unknown }>;
  return <section className="discovery-job-result" aria-label="전체 집계 결과">
    <h4>전체 집계 결과</h4>
    <p className="muted">전체 입력 · 컬럼 {String(result.column)} · 집계 {String(result.metric)}
      {result.group_by ? ` · 그룹 ${String(result.group_by)}` : " · 그룹 없음"}</p>
    <p className="hint">Dataset 판본 {String(result.version).slice(0, 12)}
      {result.truncated ? " · 표시 그룹 수 제한으로 일부 결과만 표시됩니다." : ""}</p>
    <div className="table-scroll"><table><thead><tr><th>그룹</th><th>집계값</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}>
        <td>{String(row.category ?? "전체")}</td>
        <td>{typeof row.value === "number" ? row.value.toLocaleString("ko-KR") : "계산 불가 / NULL"}</td>
      </tr>)}</tbody></table></div>
    {rows.length === 0 && <p className="muted">표시 가능한 그룹이 없습니다.</p>}
  </section>;
}
type Props = { jobs: AnyJobSummary[]; datasets: Dataset[]; busy: boolean; canCancel: boolean;
  nextCursor: string | null; discoveryNextCursor: string | null;
  onRefresh: () => void; onLoadMore: () => void; onLoadMoreDiscovery: () => void;
  onCancel: (id: string) => void; onOpen: (id: string) => void; };

export function JobsPanel({ jobs, datasets, busy, canCancel, nextCursor, discoveryNextCursor,
  onRefresh, onLoadMore, onLoadMoreDiscovery, onCancel, onOpen }: Props) {
  const [detailById, setDetailById] = useState<Record<string, Job | ExplorationJob>>({});
  const [detailErrors, setDetailErrors] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());

  async function loadDetails(jobId: string) {
    if (pending.current.has(jobId)) return;
    pending.current.add(jobId);
    setDetailErrors(current => ({ ...current, [jobId]: "" }));
    try {
      const path = jobs.find(item => item.id === jobId)?.kind === "exploration"
        ? `/api/explorations/jobs/${jobId}` : `/api/jobs/${jobId}`;
      const result = await request<Job | ExplorationJob>(path);
      setDetailById(current => ({ ...current, [jobId]: result }));
    } catch (error) {
      setDetailErrors(current => ({
        ...current,
        [jobId]: formatError(error),
      }));
    } finally {
      pending.current.delete(jobId);
    }
  }
  return <section className="panel" aria-label="실행 작업">
    <div className="panel-head"><div>
      <h3>Execution Jobs</h3>
      <p>모든 데이터셋의 작업을 조회합니다. 취소 요청과 실제 취소 완료는 구분합니다.</p>
    </div><button className="secondary" onClick={onRefresh}>새로고침</button></div>
    {jobs.length === 0 && <p className="muted">아직 제출된 작업이 없습니다. 데이터셋을 선택해 EDA 또는 Feature Job을 실행하세요.</p>}
    <div className="job-list">
      {jobs.map(job => <article className="job" key={job.id}>
        <div className="job-title"><div>
          <strong>{job.kind === "profile" ? "Full EDA Profile" :
            job.kind === "iceberg_import" ? "Iceberg Import" :
            job.kind === "exploration" ? "Full Discovery Analysis" : "Feature Transform"}</strong>
          <small>{job.id}</small>
        </div><span className={`status ${job.status.toLowerCase()}`}>
          {job.status}{job.cancel_requested ? " · 취소 요청 중" : ""}
        </span></div>
        <div className="job-metadata">
          <span>입력 데이터 · {datasets.find(d => d.id === job.dataset_id)?.name ?? job.dataset_id}</span>
          <span>등록 · {new Date(job.created_at).toLocaleString("ko-KR")}</span>
        </div>
        {job.error && <div className="job-error" role="alert">
          <p>{job.error}</p>
          {job.error_code && <small>오류 코드 · {job.error_code}</small>}
          {job.failure_stage && <small> · 단계 · {job.failure_stage}</small>}
          {job.retry_policy === "manual_verification_required" &&
            <p>커밋·Snapshot 상태를 확인하기 전에는 동일 작업을 다시 실행하지 마세요.</p>}
          {job.request_id && <p className="muted">요청 ID · {job.request_id}</p>}
        </div>}
        {typeof job.result?.row_count === "number" &&
          <p className="muted">분석한 행 · {job.result.row_count.toLocaleString("ko-KR")}</p>}
        {job.kind === "exploration" && job.result &&
          <DiscoveryJobResult result={job.result} />}
        {job.result && <details><summary>실행 결과 상세</summary>
          <pre>{JSON.stringify(job.result, null, 2)}</pre></details>}
        <details onToggle={event => { if (event.currentTarget.open) void loadDetails(job.id); }}>
          <summary>작업 설정 및 실행 시도 · {job.attempt_count}회</summary>
          <pre>{JSON.stringify(job.spec, null, 2)}</pre>
          {detailErrors[job.id] && <p role="alert">{detailErrors[job.id]}</p>}
          {!detailById[job.id] && !detailErrors[job.id] && <p role="status">상세 이력을 확인하고 있습니다.</p>}
          {detailById[job.id]?.attempts.map(attempt => <p key={attempt.id} className="muted">
            #{attempt.number} {attempt.status} · {attempt.started_at}
            {attempt.error ? ` · ${attempt.error}` : ""}
            {attempt.error_code ? ` · ${attempt.error_code}` : ""}
            {attempt.failure_stage ? ` · 단계: ${attempt.failure_stage}` : ""}
            <small> · 실행 ID: {attempt.id}</small></p>)}
        </details>
        {job.kind !== "exploration" && typeof job.result?.dataset_id === "string" &&
          <button className="secondary" onClick={() => onOpen(job.result!.dataset_id as string)}>
            결과 데이터 열기 →
          </button>}
        {job.kind === "iceberg_import" && job.status === "Running" &&
          <p className="muted">Iceberg 적재는 외부 커밋이 진행될 수 있어 실행 중 취소할 수 없습니다.</p>}
        {canCancel && ["Queued", "Running"].includes(job.status) &&
          !(job.kind === "iceberg_import" && job.status === "Running") &&
          <button className="secondary" disabled={busy || job.cancel_requested}
            onClick={() => onCancel(job.id)}>작업 취소</button>}
      </article>)}
    </div>
    {nextCursor && <button className="secondary" disabled={busy} onClick={onLoadMore}>
      이전 작업 더 보기 →
    </button>}
    {discoveryNextCursor && <button className="secondary" disabled={busy}
      onClick={onLoadMoreDiscovery}>이전 Discovery 작업 더 보기 →</button>}
  </section>;
}
