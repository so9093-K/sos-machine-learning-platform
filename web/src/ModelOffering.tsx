import { useEffect, useId, useRef, useState } from "react";
import { formatError, request } from "./api";
import { formatRecordedAt } from "./display-format";
import { isActivityModelCompatible } from "./ModelLabTaxonomy";
import type { ModelOfferingState, ModelRevision, PipelineDefinition } from "./types";

/** 모델 판본의 제공 상태를 조회한다. 제공한 적이 없으면 offered=false로 응답한다. */
export function useModelOffering(modelId: string | null, refreshKey = 0) {
  const [state, setState] = useState<{ id: string; value: ModelOfferingState | null; error: string } | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!modelId) return;
    let active = true;
    setLoading(true);
    void request<ModelOfferingState>(`/api/ml/models/${encodeURIComponent(modelId)}/offering`)
      .then(value => { if (active) setState({ id: modelId, value, error: "" }); })
      .catch(cause => { if (active) setState(previous => ({ id: modelId,
        value: previous?.id === modelId ? previous.value : null, error: formatError(cause) })); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [modelId, revision, refreshKey]);
  const current = modelId && state?.id === modelId ? state : null;
  return {
    offering: current?.value ?? null,
    error: current?.error ?? "",
    loading,
    accept: (value: ModelOfferingState) => {
      if (modelId) setState({ id: modelId, value, error: "" });
    },
    reload: () => setRevision(value => value + 1),
  };
}

function OfferingDestinations({ model, onOpenCatalog, onOpenActivity }: {
  model: ModelRevision; onOpenCatalog: () => void; onOpenActivity?: () => void;
}) {
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!onOpenActivity) return;
    let active = true;
    setDefinition(null); setError("");
    void request<PipelineDefinition[]>("/api/pipelines").then(items => {
      if (active) setDefinition(items.find(item => item.id === "activity-anomaly-if") ?? null);
    }).catch(cause => { if (active) setError(formatError(cause)); });
    return () => { active = false; };
  }, [model.id, revision, Boolean(onOpenActivity)]);
  return <div className="model-offering-destinations">
    <button type="button" className="secondary" onClick={onOpenCatalog}>제공 모델 목록 →</button>
    {onOpenActivity && definition && isActivityModelCompatible(model, definition) &&
      <button type="button" className="secondary" onClick={onOpenActivity}>이 모델로 활동 이상 탐지 →</button>}
    {error && <div role="alert"><p>분석 주제 호환성 조회 실패 · {error}</p>
      <button type="button" className="secondary" onClick={() => setRevision(value => value + 1)}>호환성 다시 조회</button></div>}
  </div>;
}

function OfferingEditor({ model, offering, disabled, onSaved }: {
  model: ModelRevision; offering: ModelOfferingState; disabled: boolean;
  onSaved: (value: ModelOfferingState) => void;
}) {
  const [description, setDescription] = useState(offering.description ?? "");
  const [operation, setOperation] = useState<"save" | "withdraw" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmWithdrawal, setConfirmWithdrawal] = useState(false);
  const mounted = useRef(true);
  const busy = useRef(false);
  const withdrawalTrigger = useRef<HTMLButtonElement>(null);
  const fieldId = useId();
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => { setDescription(offering.description ?? ""); }, [offering.description]);
  useEffect(() => { if (!offering.offered) setConfirmWithdrawal(false); }, [offering.offered]);
  const length = Array.from(description.trim()).length;
  const eligible = model.lifecycle_state === "Active" && Boolean(model.registry_version);
  async function change(action: "save" | "withdraw") {
    if (disabled || busy.current || (action === "save" && (!eligible || length < 1 || length > 1000)) ||
      (action === "withdraw" && (!offering.offered || !confirmWithdrawal))) return;
    busy.current = true; setOperation(action); setError(""); setNotice("");
    try {
      const next = await request<ModelOfferingState>(`/api/ml/models/${encodeURIComponent(model.id)}/offering`, {
        method: action === "save" ? "PUT" : "DELETE",
        ...(action === "save" ? { headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: description.trim() }) } : {}),
      });
      if (!mounted.current) return;
      setConfirmWithdrawal(false);
      onSaved(next);
      setNotice(next.offered ? "모델 제공 상태를 저장했습니다. 제공 모델 목록에서 확인할 수 있습니다."
        : "모델 제공이 중단됐습니다. 기존 결과는 유지됩니다.");
    } catch (cause) {
      if (mounted.current) setError(`요청 확인 실패 · ${formatError(cause)} · 제공 상태를 다시 조회해 접수 여부를 확인하세요.`);
    } finally {
      busy.current = false;
      if (mounted.current) setOperation(null);
    }
  }
  return <div className="model-offering-management">
    <details open={!offering.offered}>
      <summary>{offering.offered ? "제공 설명 수정" : "모델 제공 등록"}</summary>
      <p className="hint">이 모델 판본을 SOS 사용자의 신규 적용 대상으로 제공합니다. 학습 Dataset과 학습 Job은 공유되지 않습니다.</p>
      <form onSubmit={event => { event.preventDefault(); void change("save"); }}>
        <label htmlFor={fieldId}>목적·적용 대상·결과 해석과 한계</label>
        <textarea id={fieldId} value={description} rows={5} required aria-describedby={`${fieldId}-length`}
          disabled={disabled || Boolean(operation)} onChange={event => setDescription(event.target.value)}
          placeholder={"목적: 모델이 해결하는 작업\n적용 대상: 지원하는 데이터와 입력 조건\n해석·한계: 결과의 의미와 주의할 범위"} />
        <span id={`${fieldId}-length`} className="hint">{length.toLocaleString("ko-KR")} / 1,000자 · 1~1,000자 필수</span>
        {!eligible && <p className="hint">Registry 등록이 확인된 활성 모델만 제공할 수 있습니다.</p>}
        <button type="submit" disabled={disabled || Boolean(operation) || !eligible || length < 1 || length > 1000}>
          {operation === "save" ? "제공 상태 저장 중…" : offering.offered ? "제공 설명 저장" : "모델 제공"}
        </button>
      </form>
    </details>
    {offering.offered && <div className="model-offering-withdraw">
      <button ref={withdrawalTrigger} type="button" className="secondary" disabled={disabled || Boolean(operation)}
        aria-expanded={confirmWithdrawal} aria-controls={`${fieldId}-confirmation`}
        onClick={() => setConfirmWithdrawal(value => !value)}>모델 제공 중단</button>
      {confirmWithdrawal ? <div id={`${fieldId}-confirmation`} className="notice-blocked" role="group" aria-labelledby={`${fieldId}-withdraw`}>
        <h4 id={`${fieldId}-withdraw`}>모델 제공 중단 확인</h4>
        <p>대상 판본 · Registry v{model.registry_version} · {model.id}</p>
        <p>다른 사용자의 신규 모델 적용(Scoring)이 차단됩니다. 기존 결과는 유지되며, 모델은 다시 제공할 수 있습니다.</p>
        <button type="button" disabled={disabled || Boolean(operation)} onClick={() => void change("withdraw")}>
          {operation === "withdraw" ? "제공 중단 중…" : "제공 중단 확인"}</button>
        <button type="button" className="secondary" disabled={Boolean(operation)} onClick={() => {
          setConfirmWithdrawal(false); withdrawalTrigger.current?.focus();
        }}>취소</button>
      </div> : <p className="hint">제공 중단은 새 적용만 차단하며 이미 만든 결과는 유지합니다.</p>}
    </div>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}

/** 모델 상세에서 제공 상태·명시적 관리·지원되는 활용 화면을 연결한다. */
export function ModelOfferingPanel({ model, offering, error, loading, canManage, onRetry, onSaved, onOpenCatalog, onOpenActivity }: {
  model: ModelRevision;
  offering: ModelOfferingState | null;
  error: string;
  loading: boolean;
  canManage: boolean;
  onRetry: () => void;
  onSaved: (value: ModelOfferingState) => void;
  onOpenCatalog: () => void;
  onOpenActivity?: () => void;
}) {
  if (!offering && error) return <section className="model-offering" role="alert">
    <p>모델 제공 상태를 확인하지 못했습니다. {error}</p>
    <button type="button" className="secondary" onClick={onRetry}>제공 상태 다시 조회</button>
  </section>;
  if (!offering) return <p className="hint" role="status">모델 제공 상태를 확인하는 중입니다.</p>;
  return <section className="model-offering" aria-label="모델 제공 정보">
    <h3>{offering.offered ? "제공 중인 모델" : offering.withdrawn_at ? "제공이 중단된 모델" : "제공되지 않은 판본"}</h3>
    {offering.description && <p className="model-offering-description">{offering.description}</p>}
    <p className="model-offering-meta">
      {offering.offered
        ? `제공 ${formatRecordedAt(offering.offered_at ?? "")}`
        : offering.withdrawn_at ? `제공 중단 ${formatRecordedAt(offering.withdrawn_at)} · 기존 결과 유지`
          : "학습·Registry 등록과 모델 제공은 별도입니다."}
    </p>
    {loading && <p role="status">제공 상태 확인 중…</p>}
    {error && <p role="alert">제공 상태 조회 실패 · {error} · 표시는 마지막 확인 기록입니다.</p>}
    <p className="hint">모델 제공은 SOS에서 해당 판본을 사용할 수 있게 하는 작업입니다. 성능 승인이나 프로덕션 Serving 배포를 뜻하지 않습니다.</p>
    <button type="button" className="secondary" disabled={loading} onClick={onRetry}>제공 상태 다시 조회</button>
    {canManage && <OfferingEditor key={model.id} model={model} offering={offering}
      disabled={loading || Boolean(error)} onSaved={onSaved} />}
    {offering.offered && !loading && !error && <OfferingDestinations model={model}
      onOpenCatalog={onOpenCatalog} onOpenActivity={onOpenActivity} />}
  </section>;
}
