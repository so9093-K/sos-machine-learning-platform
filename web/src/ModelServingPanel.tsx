import { useEffect, useRef, useState } from "react";
import { formatError, jsonPost, request } from "./api";
import type { ModelRevision, ServingEndpoint } from "./types";

/** 선택 판본과 Endpoint 활성·전환 판본을 대조한다. 다른 판본의 ready를 재사용하지 않는다. */
export function modelServingStatus(endpoint: ServingEndpoint, modelId: string): string {
  if (endpoint.desired_state === "stopped") return "중단됨";
  const active = endpoint.active_model_revision_id === modelId;
  const pending = endpoint.pending_model_revision_id === modelId;
  if (active && endpoint.ready) return endpoint.runtime.reachable
    ? "이 판본으로 추론 가능" : "이 판본으로 추론 가능 · 운영 상태 조회 실패";
  const application = active ? endpoint.runtime.active : pending ? endpoint.runtime.pending : null;
  if (["DEPLOY_FAILED", "UNHEALTHY"].includes(application?.status ?? "")) return "이 판본 배포 실패";
  if (!endpoint.runtime.reachable) return "추론 미준비 · 운영 상태 조회 실패";
  return pending ? "이 판본 준비 중 · 활성 전환 전" : active ? "이 판본 복구 중" : "이 판본과 연결 미확인";
}

/** 기존 Serving API로 판본의 배포를 확인하고 명시적 최초 배포를 요청한다. */
export function ModelServingPanel({ model, canManage = false, onOpenServing, revision = 0 }: {
  model: ModelRevision; canManage?: boolean; onOpenServing?: () => void; revision?: number;
}) {
  const [state, setState] = useState<{ items: ServingEndpoint[] | null; error: string; loading: boolean }>({ items: null, error: "", loading: true });
  const [retry, setRetry] = useState(0);
  const [page, setPage] = useState(0);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [notice, setNotice] = useState("");
  const busy = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const items = await request<ServingEndpoint[]>(`/api/serving/endpoints?model_revision_id=${encodeURIComponent(model.id)}`);
        if (!active) return;
        setState({ items, error: "", loading: false });
        if (items.some(item => item.desired_state === "serving" &&
            (!item.ready || item.pending_model_revision_id || !item.runtime.reachable))) timer = setTimeout(() => void load(), 5000);
      } catch (cause) {
        if (!active) return;
        setState(previous => ({ ...previous, error: formatError(cause), loading: false }));
        timer = setTimeout(() => void load(), 10000);
      }
    }
    setState(previous => ({ ...previous, loading: true }));
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [model.id, revision, retry]);
  const pageCount = Math.max(1, Math.ceil((state.items?.length ?? 0) / 5));
  const currentPage = Math.min(page, pageCount - 1);
  const eligible = model.lifecycle_state === "Active" && Boolean(model.registry_version);
  async function deploy(event: React.FormEvent) {
    event.preventDefault();
    if (busy.current || !eligible || !canManage || state.loading || state.error) return;
    busy.current = true; setSubmitting(true); setSubmitError(""); setNotice("");
    try {
      const endpoint = await jsonPost<ServingEndpoint>("/api/serving/endpoints", { name: name.trim(), model_revision_id: model.id });
      if (!mounted.current) return;
      setNotice(`${endpoint.name} · 배포 요청 접수. 실제 복제본의 준비를 확인한 뒤 추론이 가능합니다.`);
      setName("");
    } catch (cause) {
      if (mounted.current) setSubmitError(`${formatError(cause)} 배포 상태를 다시 확인한 뒤 재시도하세요.`);
    } finally {
      busy.current = false;
      if (mounted.current) { setSubmitting(false); setRetry(value => value + 1); }
    }
  }
  return <section className="model-serving" aria-label="모델 온라인 배포">
    <div className="panel-head"><h3>온라인 배포</h3>
      <button type="button" className="secondary" disabled={state.loading || submitting}
        onClick={() => setRetry(value => value + 1)}>배포 상태 다시 조회</button></div>
    <p className="hint">모델 제공과 별개로, 이 판본을 메모리에 적재해 온라인 추론을 준비합니다.</p>
    {state.loading && <p role="status">배포 상태 조회 중…</p>}
    {state.error && <p role="alert">배포 상태 조회 실패 · {state.error}{state.items ? " · 표시는 마지막 확인 기록입니다." : ""}</p>}
    {!state.loading && !state.error && state.items?.length === 0 && <p>조회 가능한 이 판본의 온라인 배포 기록이 없습니다.</p>}
    {state.items && <ul className="model-serving-list">{state.items.slice(currentPage * 5, (currentPage + 1) * 5).map(endpoint => <li key={endpoint.id}>
      <strong>{endpoint.name}</strong><span>{modelServingStatus(endpoint, model.id)}</span>
      {endpoint.manageable && (endpoint.active_model_revision_id === model.id
        ? endpoint.runtime.active : endpoint.runtime.pending)?.status_reason && <small>{
        (endpoint.active_model_revision_id === model.id ? endpoint.runtime.active : endpoint.runtime.pending)?.status_reason}</small>}
    </li>)}</ul>}
    {pageCount > 1 && <div className="feature-result-pages">
      <span>{currentPage + 1} / {pageCount} · 한 번에 5개</span>
      <button type="button" className="secondary" disabled={currentPage === 0}
        onClick={() => setPage(currentPage - 1)}>이전 Endpoint</button>
      <button type="button" className="secondary" disabled={currentPage + 1 === pageCount}
        onClick={() => setPage(currentPage + 1)}>다음 Endpoint</button>
    </div>}
    {canManage && onOpenServing && <button type="button" className="secondary" onClick={onOpenServing}>온라인 서빙 운영 →</button>}
    {canManage && <details className="model-deployment-form"><summary>이 판본의 새 Endpoint 배포</summary>
      <p>대상 · {model.id} · {model.registry_version ? `Registry v${model.registry_version}` : "Registry 등록 미확인"}</p>
      <p className="hint">복제본이 Runtime 자원을 사용합니다. 다른 사용자 대상 제공 상태는 바뀌지 않습니다.</p>
      {!eligible && <p>활성 모델의 Registry 등록 확인 후 배포할 수 있습니다.</p>}
      <form onSubmit={event => void deploy(event)}>
        <label>Endpoint 이름<input required minLength={2} maxLength={63} pattern="[a-z0-9][a-z0-9-]*[a-z0-9]"
          value={name} disabled={submitting} onChange={event => setName(event.target.value)}
          placeholder="예: activity-service" /></label>
        <p className="hint">영문 소문자·숫자·하이픈, 2–63자 · 처음과 끝은 영문 또는 숫자</p>
        <button type="submit" disabled={!eligible || submitting || state.loading || Boolean(state.error)}>
          {submitting ? "배포 요청 중…" : "Endpoint 배포 요청"}</button>
      </form>
    </details>}
    {submitError && <p role="alert">{submitError}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
