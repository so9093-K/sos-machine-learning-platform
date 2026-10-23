/** 생성 모델을 해당 Pipeline 문맥 안에서 독립 Scoring 기능에 연결한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { MLScoringPanel } from "./MLScoringPanel";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelRevision } from "./types";

export function PipelineModelUsage({ modelId, datasets, canRun, canCancel, onOpenResource }: {
  modelId: string;
  datasets: Dataset[];
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
      <span className="eyebrow">모델 활용 · 추가 실행</span>
      <h3 id="pipeline-model-usage-title">생성한 모델을 다른 데이터에 적용하기</h3>
      <p>이번 분석에서 학습한 모델을 재학습하지 않고, 호환 Dataset에 독립적으로 점수화합니다.
        새 Scoring 결과는 기존 Pipeline Test 결과를 변경하지 않습니다.</p>
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
      datasets={datasets} canRun={canRun} canCancel={canCancel}
      onOpenResult={id => onOpenResource("scoring", id)} />}
  </section>;
}
