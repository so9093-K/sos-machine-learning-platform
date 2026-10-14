import type { Job } from "./types";

type Props = { jobs: Job[]; busy: boolean; onRefresh: () => void;
  onCancel: (id: string) => void; onOpen: (id: string) => void; };

export function JobsPanel({ jobs, busy, onRefresh, onCancel, onOpen }: Props) {
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
        {job.error && <p className="job-error" role="alert">{job.error}</p>}
        {job.result && <pre>{JSON.stringify(job.result, null, 2)}</pre>}
        {job.attempts.length > 0 && <span className="muted">실행 시도 {job.attempts.length}회</span>}
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
  </section>;
}
