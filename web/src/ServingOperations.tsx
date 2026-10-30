import { useCallback, useEffect, useState } from "react";
import { formatError, request } from "./api";
import { formatRecordedAt } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ServingEndpoint } from "./types";

type Action = "rollback" | "cancel" | "stop" | "resume" | "delete";
// 전환·복구 중에는 컨트롤러의 판정을 짧게 따라가고, 장애·실패 중에는 복구를 확인할 만큼만 다시 조회한다.
const SETTLING_POLL_MS = 3000;
const RECOVERY_POLL_MS = 10000;
// 서빙 Runtime(Ray Serve)이 보고하는 애플리케이션 상태.
const runtimeLabels: Record<string, string> = {
  RUNNING: "실행 중", DEPLOYING: "준비 중", NOT_DEPLOYED: "배포 대기", DEPLOY_FAILED: "배포 실패",
  UNHEALTHY: "비정상", DELETING: "정리 중",
};

/** 실제 실행 상태를 운영자가 바로 판단할 수 있는 문장으로 바꾼다. */
function endpointStatus(endpoint: ServingEndpoint): { label: string; tone: "ready" | "pending" | "failed" | "idle" } {
  const { runtime } = endpoint;
  if (endpoint.desired_state === "stopped") return { label: "중단됨", tone: "idle" };
  if (!runtime.reachable) return { label: "서빙 Runtime 연결 안 됨", tone: "failed" };
  const pendingFailed = runtime.pending?.status === "DEPLOY_FAILED" || runtime.pending?.status === "UNHEALTHY";
  if (!endpoint.active_model_revision_id) return pendingFailed
    ? { label: "첫 배포 실패", tone: "failed" } : { label: "첫 배포 준비 중", tone: "pending" };
  if (!endpoint.ready) return { label: "활성 판본 복구 중", tone: "pending" };
  if (endpoint.pending_model_revision_id) return pendingFailed
    ? { label: "전환 실패 · 현재 판본 유지", tone: "failed" } : { label: "판본 전환 중", tone: "pending" };
  return { label: "요청 처리 중", tone: "ready" };
}

/** 온라인 서빙 Endpoint의 실제 상태와 운영 조작.
 *
 *  배포와 새 판본 전환은 모델 공급 과정(scripts/supply_model.py --endpoint)이 맡는다.
 *  이 화면은 지금 어떤 판본이 요청을 처리하는지 확인하고 되돌리기·중단 같은 안전 조작을 제공한다.
 */
export function ServingOperations({ onOpenResource }: {
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [endpoints, setEndpoints] = useState<ServingEndpoint[] | null>(null);
  // 목록 조회 실패와 조작 실패는 따로 둔다. 조작 뒤 재조회가 성공해도 조작 실패 안내는 남아야 한다.
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [confirming, setConfirming] = useState<string>("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setEndpoints(await request<ServingEndpoint[]>("/api/serving/endpoints"));
      setLoadError("");
    } catch (cause) {
      setLoadError(formatError(cause));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const tones = endpoints?.map(item => endpointStatus(item).tone) ?? [];
  const pollMs = tones.includes("pending") ? SETTLING_POLL_MS
    : tones.includes("failed") || loadError ? RECOVERY_POLL_MS : null;
  useEffect(() => {
    if (pollMs === null) return;
    const timer = window.setInterval(() => void load(), pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs, load]);

  async function run(endpoint: ServingEndpoint, action: Action) {
    const key = `${endpoint.id}:${action}`;
    if ((action === "stop" || action === "delete") && confirming !== key) { setConfirming(key); return; }
    setConfirming(""); setBusy(key); setNotice(""); setActionError("");
    const base = `/api/serving/endpoints/${encodeURIComponent(endpoint.id)}`;
    const json = (body: unknown) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    try {
      if (action === "rollback") await request(`${base}/rollback`, { method: "POST" });
      if (action === "cancel") await request(`${base}/revision-request`, { method: "DELETE" });
      if (action === "stop") await request(`${base}/state`, { method: "PUT", ...json({ desired_state: "stopped" }) });
      if (action === "resume") await request(`${base}/state`, { method: "PUT", ...json({ desired_state: "serving" }) });
      if (action === "delete") await request(base, { method: "DELETE" });
      setNotice({
        rollback: `${endpoint.name} · 직전 판본을 준비합니다. 준비되기 전까지 현재 판본이 요청을 처리합니다.`,
        cancel: `${endpoint.name} · 판본 전환을 취소했습니다. 현재 판본이 계속 요청을 처리합니다.`,
        stop: `${endpoint.name} · 중단했습니다. 판본 기록은 유지됩니다.`,
        resume: `${endpoint.name} · 재개를 요청했습니다. 준비되면 요청을 처리합니다.`,
        delete: `${endpoint.name} · Endpoint를 삭제했습니다.`,
      }[action]);
    } catch (cause) {
      setActionError(`${endpoint.name} · ${formatError(cause)}`);
    } finally {
      setBusy("");
      await load();
    }
  }

  const revision = (label: string, id: string | null | undefined, detail?: { status: string; failure_reason?: string | null } | null) =>
    id ? <div className="serving-revision">
      <dt>{label}</dt>
      <dd>
        <button type="button" className="ghost" onClick={() => onOpenResource("model", id)}>{id.slice(0, 8)}</button>
        {detail && <span className="muted"> · {runtimeLabels[detail.status] ?? detail.status}</span>}
        {detail?.failure_reason && <span className="serving-failure" role="note">{detail.failure_reason}</span>}
      </dd>
    </div> : null;

  return <section className="panel serving-operations" aria-labelledby="serving-heading">
    <div className="panel-head">
      <div><h2 id="serving-heading">온라인 서빙 Endpoint</h2>
        <p>Endpoint마다 지금 요청을 처리하는 판본과 실제 실행 상태입니다. 새 판본은 준비가 확인된 뒤에만 전환되며,
          준비에 실패하면 현재 판본이 계속 요청을 처리합니다. 배포와 새 판본 전환은 모델 공급 과정에서 요청합니다.</p>
      </div>
      <button type="button" className="secondary" onClick={() => void load()}>다시 조회</button>
    </div>
    {loadError && <p className="job-error" role="alert">상태 조회 실패 · {loadError}{endpoints
      ? " · 아래는 마지막으로 확인한 상태이며, 10초마다 다시 확인합니다." : " · 10초마다 다시 확인합니다."}</p>}
    {actionError && <p className="job-error" role="alert">요청 실패 · {actionError}</p>}
    {notice && <p className="hint" role="status">{notice}</p>}
    {endpoints === null && !loadError && <p role="status" className="muted">Endpoint를 불러오는 중입니다.</p>}
    {endpoints?.length === 0 && <p className="muted">아직 Endpoint가 없습니다. 모델 공급 과정에서 Endpoint를 지정하면 이곳에 표시됩니다.</p>}
    <div className="serving-list">{endpoints?.map(endpoint => {
      const status = endpointStatus(endpoint);
      const pending = (action: Action) => busy === `${endpoint.id}:${action}`;
      const disabled = Boolean(busy) || !endpoint.manageable;
      const confirm = (action: Action, text: string, verb: string) => confirming === `${endpoint.id}:${action}` &&
        <div className="serving-confirm" role="group" aria-label={`${endpoint.name} ${verb} 확인`}>
          <span>{text}</span>
          <button type="button" disabled={disabled} onClick={() => void run(endpoint, action)}>{verb}</button>
          <button type="button" className="ghost" onClick={() => setConfirming("")}>취소</button>
        </div>;
      return <article key={endpoint.id} className="serving-endpoint" aria-label={endpoint.name}>
        <div className="serving-endpoint-head">
          <h3>{endpoint.name}</h3>
          <span className={`serving-status serving-status-${status.tone}`}>{status.label}</span>
          {!endpoint.owned && endpoint.owner_actor && <span className="muted">소유 계정 {endpoint.owner_actor}</span>}
          <span className="muted serving-updated">갱신 {formatRecordedAt(endpoint.updated_at)}</span>
        </div>
        <dl>
          {revision("활성 판본", endpoint.active_model_revision_id, endpoint.runtime.active)}
          {revision(endpoint.active_model_revision_id ? "전환 대상" : "첫 배포 판본",
            endpoint.pending_model_revision_id, endpoint.runtime.pending)}
          {revision("직전 판본", endpoint.previous_model_revision_id)}
        </dl>
        {endpoint.manageable && <div className="serving-actions">
          {endpoint.previous_model_revision_id && !endpoint.pending_model_revision_id && endpoint.desired_state === "serving" &&
            <button type="button" className="secondary" disabled={disabled} onClick={() => void run(endpoint, "rollback")}>
              {pending("rollback") ? "요청 중…" : "직전 판본으로 되돌리기"}</button>}
          {endpoint.pending_model_revision_id && endpoint.active_model_revision_id &&
            <button type="button" className="secondary" disabled={disabled} onClick={() => void run(endpoint, "cancel")}>
              {pending("cancel") ? "요청 중…" : "전환 취소"}</button>}
          {endpoint.desired_state === "serving"
            ? <button type="button" className="secondary" disabled={disabled} onClick={() => void run(endpoint, "stop")}>
              {pending("stop") ? "요청 중…" : "중단"}</button>
            : <>
              <button type="button" className="secondary" disabled={disabled} onClick={() => void run(endpoint, "resume")}>
                {pending("resume") ? "요청 중…" : "재개"}</button>
              <button type="button" className="secondary" disabled={disabled} onClick={() => void run(endpoint, "delete")}>
                {pending("delete") ? "요청 중…" : "삭제"}</button>
            </>}
        </div>}
        {confirm("stop", "중단하면 이 Endpoint로 들어오는 요청이 실패합니다.", "중단")}
        {confirm("delete", "삭제하면 Endpoint 주소가 사라집니다. 모델 판본은 유지됩니다.", "삭제")}
      </article>;
    })}</div>
  </section>;
}
