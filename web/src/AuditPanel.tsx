import { useEffect, useState } from "react";
import { request, formatError } from "./api";

type AuditEvent = {
  id: string;
  at: string;
  actor_id: string;
  actor_type: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  decision: string;
  outcome: string;
  reason: string | null;
};

export function AuditPanel() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [jobId, setJobId] = useState("");

  async function reload() {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "50" });
      if (requestId.trim()) params.set("request_id", requestId.trim());
      if (jobId.trim()) params.set("job_id", jobId.trim());
      setEvents(await request<AuditEvent[]>(`/api/audit?${params.toString()}`));
    } catch (exc) {
      setError(formatError(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);
  return <section className="panel" aria-label="보안 감사">
    <div className="panel-head">
      <div><h3>Security Audit</h3>
        <p>권한 판단과 작업 변경의 최근 감사 기록입니다. 원본 토큰과 비밀키는 표시하지 않습니다.</p>
      </div>
      <button className="secondary" disabled={loading} onClick={() => void reload()}>
        기록 새로고침
      </button>
    </div>
    <div className="form-grid">
      <label>Request ID
        <input aria-label="감사 Request ID" value={requestId}
          onChange={event => setRequestId(event.target.value)}
          placeholder="HTTP 응답의 요청 ID" />
      </label>
      <label>Job ID
        <input aria-label="감사 Job ID" value={jobId}
          onChange={event => setJobId(event.target.value)}
          placeholder="Job의 ID" />
      </label>
      <button className="secondary" disabled={loading} onClick={() => void reload()}>
        감사 기록 검색
      </button>
    </div>
    {error && <p role="alert">{error}</p>
    {loading && <p role="status">감사 기록 조회 중</p>}
    {events.length === 0 && !loading && <p className="muted">조회 가능한 감사 이벤트가 없습니다.</p>}
    <div className="job-list">
      {events.map(event => <article className="job" key={event.id}>
        <strong>{event.action} · {event.outcome}</strong>
        <p className="muted">{event.actor_type} · {event.actor_id} · {event.decision}</p>
        <small>{event.resource_type} {event.resource_id ?? ""} ·
          {new Date(event.at).toLocaleString("ko-KR")}</small>
      </article>)}
    </div>
  </section>;
}
