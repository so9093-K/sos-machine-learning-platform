/** 생성 모델을 해당 Pipeline 문맥 안에서 독립 Scoring 기능에 연결한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { MLScoringPanel } from "./MLScoringPanel";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelRevision } from "./types";

export function PipelineModelUsage({
  modelId, datasets, hasMoreDatasets = false, onLoadMoreDatasets, canRun, canCancel, onOpenResource,
}: {
  modelId: string;
  datasets: Dataset[];
  hasMoreDatasets?: boolean;
  onLoadMoreDatasets?: () => Promise<void>;
  canRun: boolean;
  canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{
    id: string; model: ModelRevision | null; error: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    setState({ id: modelId, model: null, error: "" });
    void request<ModelRevision>("/api/ml/models/" + encodeURIComponent(modelId))
      .then(model => {
        if (active) setState({ id: modelId, model, error: "" });
      }).catch(error => {
        if (active) setState({ id: modelId, model: null, error: formatError(error) });
      });
    return () => { active = false; };
  }, [modelId, retry]);

  const current = state?.id === modelId ? state : null;
  return <section className="panel pipeline-model-usage" aria-labelledby="pipeline-model-usage-title">
    <div className="pipeline-section-heading"><div>
      <span className="eyebrow">모델 실험실 · 모델 체험</span>
      <h3 id="pipeline-model-usage-title" tabIndex={-1}>학습한 모델 사용해보기</h3>
      <p>Dataset을 선택하고 기존 모델로 실제 이상 점수를 계산합니다.
        재학습하지 않으며 원래 Test 결과를 변경하지 않습니다.</p>
    </div></div>
    {!current && <p role="status">학습 모델을 조회하는 중입니다.</p>}
    {current?.error && <div role="alert" className="pipeline-readiness-blocked">
      <strong>학습 모델을 불러오지 못했습니다.</strong>
      <p>{current.error}</p>
      <button className="secondary" type="button" onClick={() => setRetry(n => n + 1)}>
        모델 다시 조회
      </button>
    </div>}
    {current?.model && <MLScoringPanel key={modelId} model={current.model}
      datasets={datasets} hasMoreDatasets={hasMoreDatasets}
      onLoadMoreDatasets={onLoadMoreDatasets}
      canRun={canRun} canCancel={canCancel}
      onOpenResult={id => onOpenResource("scoring", id)} />}
  </section>;
}
