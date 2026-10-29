import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { formatRecordedAt } from "./display-format";
import type { ModelOfferingState } from "./types";

/** 모델 판본의 제공 상태를 조회한다. 제공한 적이 없으면 offered=false로 응답한다. */
export function useModelOffering(modelId: string | null) {
  const [state, setState] = useState<{ id: string; value: ModelOfferingState | null; error: string } | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!modelId) return;
    let active = true;
    void request<ModelOfferingState>(`/api/ml/models/${encodeURIComponent(modelId)}/offering`)
      .then(value => { if (active) setState({ id: modelId, value, error: "" }); })
      .catch(cause => { if (active) setState({ id: modelId, value: null, error: formatError(cause) }); });
    return () => { active = false; };
  }, [modelId, revision]);
  const current = modelId && state?.id === modelId ? state : null;
  return {
    offering: current?.value ?? null,
    error: current?.error ?? "",
    reload: () => setRevision(value => value + 1),
  };
}

/** 모델을 고를 때 읽는 제공 설명과 상태. 제공·중단은 개발·배포 과정이 관리한다. */
export function ModelOfferingPanel({ offering, error, onRetry }: {
  offering: ModelOfferingState | null;
  error: string;
  onRetry: () => void;
}) {
  if (error) return <section className="model-offering" role="alert">
    <p>모델 제공 상태를 확인하지 못했습니다. {error}</p>
    <button type="button" className="secondary" onClick={onRetry}>제공 상태 다시 조회</button>
  </section>;
  if (!offering) return <p className="hint" role="status">모델 제공 상태를 확인하는 중입니다.</p>;
  if (!offering.description) return <section className="model-offering" aria-label="모델 제공 정보">
    <h3>제공되지 않은 판본</h3>
    <p>다른 사용자에게 제공되지 않은 판본입니다. 사용자에게 제공하는 일은 개발·배포 과정에서 관리합니다.</p>
  </section>;
  return <section className="model-offering" aria-label="모델 제공 정보">
    <h3>{offering.offered ? "모델 목적·적용 대상·한계" : "제공이 중단된 모델"}</h3>
    <p className="model-offering-description">{offering.description}</p>
    <p className="model-offering-meta">
      {offering.offered
        ? `제공 ${formatRecordedAt(offering.offered_at ?? "")}`
        : `제공 중단 ${formatRecordedAt(offering.withdrawn_at ?? "")} · 새 적용은 할 수 없고 이미 만든 결과는 계속 확인할 수 있습니다.`}
    </p>
  </section>;
}
