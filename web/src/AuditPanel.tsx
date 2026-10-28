import { useEffect, useState } from "react";
import { request, formatError } from "./api";
import type { AuditEvent } from "./types";
import { formatRecordedAt } from "./display-format";

const actionLabels: Record<string, string> = {
  "security.access_denied": "권한 거부", "security.resource_access_denied": "리소스 접근 거부",
  "dataset.create": "Dataset 등록", "dataset.publish": "Dataset 게시",
  "job.create": "Job 생성", "job.cancel": "Job 취소 요청", "job.finish": "Job 종료",
  "job.recover": "Job 복구", "workflow.submit": "워크플로 제출",
  "model.publish": "모델 게시", "model.delete_requested": "모델 삭제 요청",
  "model.permanently_deleted": "모델 영구 삭제", "scoring.publish": "점수 결과 게시",
  "policy.publish": "판정 정책 게시", "evaluation.publish": "평가 결과 게시",
  "explanation.publish": "설명 결과 게시", "mlflow.export": "MLflow 기록",
  "snapshot.retain": "Snapshot 보존", "instance.configure": "인스턴스 설정",
};
const outcomeLabels: Record<string, string> = {
  succeeded: "성공", failed: "실패", denied: "거부", cancelled: "취소됨",
  requeued: "재대기", interrupted: "중단됨",
};
const actorTypeLabels: Record<string, string> = { human: "사용자", service: "서비스" };

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
      setEvents(await request<AuditEvent[]>(`/api/v2/audit?${params.toString()}`));
    } catch (exc) {
      setError(formatError(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);
  return <section className="panel" aria-label="보안 감사">
    <div className="panel-head">
      <div><h2>보안 감사 기록</h2>
        <p>권한 판단과 작업 변경의 최근 감사 기록입니다. 원본 토큰과 비밀키는 표시하지 않습니다.</p>
      </div>
      <button className="secondary" disabled={loading} onClick={() => void reload()}>
        기록 새로고침
      </button>
    </div>
    <div className="form-grid">
      <label>요청 ID
        <input aria-label="감사 요청 ID" value={requestId}
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
    {error && <p role="alert">{error}</p>}
    {loading && <p role="status">감사 기록 조회 중</p>}
    {events.length === 0 && !loading && <p className="muted">조회 가능한 감사 이벤트가 없습니다.</p>}
    {events.length > 0 && <div className="table-scroll audit-table"><table>
      <thead><tr><th>시각</th><th>이벤트</th><th>결과</th><th>행위자</th><th>대상</th></tr></thead>
      <tbody>{events.map(event => <tr key={event.id}
        className={event.decision === "deny" || event.outcome === "failed" ? "audit-flagged" : undefined}>
        <td>{formatRecordedAt(event.at)}</td>
        <td title={event.action}>{actionLabels[event.action] ?? event.action}
          {event.reason && <small>{event.reason}</small>}</td>
        <td>{outcomeLabels[event.outcome] ?? event.outcome}</td>
        <td>{event.actor_id}<small>{actorTypeLabels[event.actor_type] ?? event.actor_type}</small></td>
        <td title={event.resource_id ?? undefined}>{event.resource_type}
          {event.resource_id && <small>{event.resource_id.slice(0, 12)}</small>}
          {event.request_id && <small>요청 {event.request_id.slice(0, 12)}</small>}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
