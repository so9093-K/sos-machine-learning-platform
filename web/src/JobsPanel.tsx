import type { Dataset, Job } from "./types";

type Props = { jobs: Job[]; datasets: Dataset[]; busy: boolean; nextCursor: string | null;
  onRefresh: () => void; onLoadMore: () => void;
  onCancel: (id: string) => void; onOpen: (id: string) => void; };

export function JobsPanel({ jobs, datasets, busy, nextCursor, onRefresh, onLoadMore, onCancel, onOpen }: Props) {
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
            job.kind === "iceberg_import" ? "Iceberg Import" : "Feature Transform"}</strong>
          <small>{job.id}</small>
        </div><span className={`status ${job.status.toLowerCase()}`}>
          {job.status}{job.cancel_requested ? " · 취소 요청 중" : ""}
        </span></div>
        <div className="job-metadata">
          <span>입력 데이터 · {datasets.find(d => d.id === job.dataset_id)?.name ?? job.dataset_id}</span>
          <span>등록 · {new Date(job.created_at).toLocaleString("ko-KR")}</span>
        </div>
        {job.error && <p className="job-error" role="alert">{job.error}</p>}
        {typeof job.result?.row_count === "number" &&
          <p className="muted">분석한 행 · {job.result.row_count.toLocaleString("ko-KR")}</p>}
        {job.result && <details><summary>실행 결과 상세</summary>
          <pre>{JSON.stringify(job.result, null, 2)}</pre></details>}
        <details><summary>작업 설정 및 실행 시도 · {job.attempts.length}회</summary>
          <pre>{JSON.stringify(job.spec, null, 2)}</pre>
          {job.attempts.map(attempt => <p key={attempt.id} className="muted">
            #{attempt.number} {attempt.status} · {attempt.started_at}
            {attempt.error ? ` · ${attempt.error}` : ""}</p>)}
        </details>
        {typeof job.result?.dataset_id === "string" &&
          <button className="secondary" onClick={() => onOpen(job.result!.dataset_id as string)}>
            결과 데이터 열기 →
          </button>}
        {job.kind === "iceberg_import" && job.status === "Running" &&
          <p className="muted">Iceberg 적재는 외부 커밋이 진행될 수 있어 실행 중 취소할 수 없습니다.</p>}
        {["Queued", "Running"].includes(job.status) &&
          !(job.kind === "iceberg_import" && job.status === "Running") &&
          <button className="secondary" disabled={busy || job.cancel_requested}
            onClick={() => onCancel(job.id)}>작업 취소</button>}
      </article>)}
    </div>
    {nextCursor && <button className="secondary" disabled={busy} onClick={onLoadMore}>
      이전 작업 더 보기 →
    </button>}
  </section>;
}
