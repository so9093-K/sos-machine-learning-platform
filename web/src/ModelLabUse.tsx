/** 현재 Actor가 사용할 수 있는 Registry 모델 중 활동 이상 탐지 입력 계약에 맞는 모델만 사용한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { MLScoringPanel } from "./MLScoringPanel";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, MLflowModelVersion, MLflowModelVersionPage, ModelRevision, PipelineDefinition } from "./types";

type RegisteredActivityModel = { version: MLflowModelVersion; revision: ModelRevision };

export function isActivityModelCompatible(model: Pick<ModelRevision, "algorithm" | "features">,
  definition: Pick<PipelineDefinition, "algorithm" | "required_features">): boolean {
  return model.algorithm === definition.algorithm &&
    model.features.length === definition.required_features.length &&
    model.features.every((feature, index) => feature === definition.required_features[index]);
}

async function compatiblePage(page: MLflowModelVersionPage, definition: PipelineDefinition) {
  const checked = await Promise.allSettled(page.items
    .filter(version => version.status === "READY")
    .map(async version => ({
      version,
      revision: await request<ModelRevision>("/api/ml/models/" + encodeURIComponent(version.model_id)),
    })));
  return {
    items: checked.filter((result): result is PromiseFulfilledResult<RegisteredActivityModel> =>
      result.status === "fulfilled").map(result => result.value)
      .filter(({ revision }) => isActivityModelCompatible(revision, definition)),
    failed: checked.filter(result => result.status === "rejected").length,
  };
}

export function ModelLabUse({ datasets, hasMoreDatasets, onLoadMoreDatasets, canRun, canCancel,
  selectedModelId, onSelectModel, onOpenResource, onBack, onLearn }: {
  datasets: Dataset[];
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  canRun: boolean; canCancel: boolean;
  selectedModelId: string;
  onSelectModel: (id: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onBack: () => void; onLearn: () => void;
}) {
  const [models, setModels] = useState<RegisteredActivityModel[]>([]);
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [error, setError] = useState("");
  const [lookupFailures, setLookupFailures] = useState(0);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setDefinition(null);
    setModels([]); setNextPage(null); setLookupFailures(0);
    void Promise.all([
      request<PipelineDefinition[]>("/api/pipelines"),
      request<MLflowModelVersionPage>("/api/ml/registry/models"),
    ]).then(async ([definitions, page]) => {
      const current = definitions.find(item => item.id === "activity-anomaly-if");
      if (!current) throw new Error("활동 이상 탐지 분석 방법을 찾을 수 없습니다.");
      const checked = await compatiblePage(page, current);
      if (active) {
        setDefinition(current);
        setModels(checked.items);
        setLookupFailures(checked.failed);
        setNextPage(page.next_page_token ?? null);
      }
    }).catch(cause => { if (active) setError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  async function loadMore() {
    if (!definition || !nextPage || moreLoading) return;
    const token = nextPage;
    setMoreLoading(true); setError("");
    try {
      const page = await request<MLflowModelVersionPage>(
        "/api/ml/registry/models?page_token=" + encodeURIComponent(token));
      const checked = await compatiblePage(page, definition);
      setModels(previous => {
        const seen = new Set(previous.map(item => item.revision.id));
        return [...previous, ...checked.items.filter(item => !seen.has(item.revision.id))];
      });
      setLookupFailures(previous => previous + checked.failed);
      setNextPage(page.next_page_token ?? null);
    } catch (cause) {
      setError(formatError(cause));
    } finally { setMoreLoading(false); }
  }

  const selected = models.find(item => item.revision.id === selectedModelId) ?? null;
  return <section className="model-lab-use" aria-labelledby="model-lab-use-heading">
    <button type="button" className="ghost model-lab-back" onClick={onBack}>
      ← 활동 이상 탐지 소개로
    </button>
    <header className="model-lab-use-heading">
      <div>
        <span className="eyebrow">활동 이상 탐지 · 모델 활용</span>
        <h2 id="model-lab-use-heading">학습된 모델 사용해보기</h2>
        <p>등록된 모델을 선택해 다른 호환 Dataset에 적용하고 이상 점수를 계산합니다.</p>
      </div>
      <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}
        disabled={loading || moreLoading}>모델 목록 새로고침</button>
    </header>
    {loading && <p role="status">사용 가능한 모델을 확인하는 중입니다.</p>}
    {error && <div className="pipeline-readiness-blocked" role="alert">
      <p>{models.length > 0 || nextPage
        ? "추가 모델 판본을 불러오지 못했습니다: "
        : "모델 목록을 확인하지 못했습니다: "}{error}</p>
      <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}>
        다시 조회
      </button>
    </div>}
    {!loading && !error && lookupFailures > 0 &&
      <div className="pipeline-readiness-blocked" role="status">
        일부 등록 모델 정보를 확인하지 못했습니다. 누락된 모델은 표시하지 않았습니다.
        <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}>
          다시 조회
        </button>
      </div>}
    {!loading && !error && models.length === 0 && <div className="model-lab-no-models">
      <h3>{nextPage || lookupFailures ? "현재 조회한 모델 중 호환되는 모델이 없습니다" :
        "아직 사용할 수 있는 활동 이상 탐지 모델이 없습니다"}</h3>
      <p>처음 시작한다면 예제 데이터로 모델을 한 번 학습해 보세요.
        학습 후 등록이 완료되면 여기에서 해당 모델을 선택할 수 있습니다.</p>
      <button type="button" onClick={onLearn}>예제로 모델 학습하기 →</button>
    </div>}
    {models.length > 0 && <div className="model-lab-registered-list">
      <h3>사용 가능한 모델 판본</h3>
      <div className="model-lab-model-list">
        {models.map(({ revision, version }) => <button key={revision.id} type="button"
          className="model-lab-model-choice" aria-pressed={selectedModelId === revision.id}
          onClick={() => onSelectModel(revision.id)}>
          <span><strong>Isolation Forest</strong>
            <small>학습일 {revision.created_at.slice(0, 10)} · MLflow v{version.version}</small></span>
          <span>{selectedModelId === revision.id ? "선택됨" : "선택 →"}</span>
        </button>)}
      </div>
    </div>}
    {!loading && nextPage && <button type="button" className="secondary" disabled={moreLoading}
      onClick={() => void loadMore()}>{moreLoading ? "모델 확인 중…" : "다음 모델 판본 조회"}</button>}
    {!loading && !error && selectedModelId && !selected &&
      <div className="pipeline-readiness-blocked" role="status">
        <p>URL에서 선택한 모델은 현재 조회된 호환 모델 목록에 없습니다.
          다음 모델 판본을 조회하거나 목록을 새로고침해 주세요.</p>
        <button type="button" className="secondary"
          onClick={() => onSelectModel("")}>선택 해제</button>
      </div>}
    {!loading && !selected && !selectedModelId && models.length > 0 &&
      <p className="hint">모델 판본을 선택하면 적용할 Dataset을 고를 수 있습니다.</p>}
    {selected && <MLScoringPanel key={selected.revision.id} model={selected.revision}
      datasets={datasets} hasMoreDatasets={hasMoreDatasets}
      onLoadMoreDatasets={onLoadMoreDatasets} canRun={canRun} canCancel={canCancel}
      onOpenResult={id => onOpenResource("scoring", id)} />}
    <div className="model-lab-use-footer">
      <button type="button" className="ghost" onClick={onLearn}>새 모델 학습해보기 →</button>
    </div>
  </section>;
}
