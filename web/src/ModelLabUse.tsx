/** 제공된 모델 중 활동 이상 탐지 입력 계약에 맞는 판본을 사용자의 데이터에 적용한다. */
import { useCallback, useEffect, useState } from "react";
import { formatError, request } from "./api";
import { ModelApplicationWorkspace } from "./ModelApplicationWorkspace";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelOffering, ModelOfferingPage, ModelRevision, PipelineDefinition } from "./types";

export function isActivityModelCompatible(model: Pick<ModelRevision, "algorithm" | "features">,
  definition: Pick<PipelineDefinition, "algorithm" | "required_features">): boolean {
  return model.algorithm === definition.algorithm &&
    model.features.length === definition.required_features.length &&
    model.features.every((feature, index) => feature === definition.required_features[index]);
}

async function offeredPage(cursor: string | null) {
  return request<ModelOfferingPage>("/api/ml/offered-models?limit=50" +
    (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""));
}

export function ModelLabUse({ selectedDatasetId, onSelectDataset, datasets, hasMoreDatasets, onLoadMoreDatasets, canRun, canCancel,
  selectedModelId, onSelectModel, onOpenResource, onHistory, canUpload, onDatasetRegistered }: {
  selectedDatasetId?: string;
  onSelectDataset?: (id: string) => void;
  datasets: Dataset[];
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  canRun: boolean; canCancel: boolean;
  selectedModelId: string;
  onSelectModel: (id: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onHistory: () => void;
  canUpload: boolean;
  onDatasetRegistered: (dataset: Dataset) => void;
}) {
  const [models, setModels] = useState<ModelOffering[]>([]);
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [resultFocusedModelId, setResultFocusedModelId] = useState<string | null>(null);
  const [showResultSetup, setShowResultSetup] = useState(false);
  const handleResultViewChange = useCallback((visible: boolean) => {
    setResultFocusedModelId(visible ? selectedModelId : null);
  }, [selectedModelId]);
  const compatible = (page: ModelOfferingPage, current: PipelineDefinition) =>
    page.items.filter(item => isActivityModelCompatible(item.model, current));
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setDefinition(null); setModels([]); setCursor(null);
    void Promise.all([request<PipelineDefinition[]>("/api/pipelines"), offeredPage(null)])
      .then(([definitions, page]) => {
        const current = definitions.find(item => item.id === "activity-anomaly-if");
        if (!current) throw new Error("활동 이상 탐지 분석 방법을 찾을 수 없습니다.");
        if (!active) return;
        const items = compatible(page, current);
        setDefinition(current);
        setModels(items);
        setCursor(page.next_cursor ?? null);
        if (!selectedModelId && items.length) onSelectModel(items[0].model.id);
      })
      .catch(cause => { if (active) setError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  async function loadMore() {
    if (!definition || !cursor || moreLoading) return;
    setMoreLoading(true); setError("");
    try {
      const page = await offeredPage(cursor);
      const items = compatible(page, definition);
      setModels(previous => [...previous, ...items.filter(item =>
        !previous.some(entry => entry.model.id === item.model.id))]);
      setCursor(page.next_cursor ?? null);
      if (!selectedModelId && items.length) onSelectModel(items[0].model.id);
    } catch (cause) { setError(formatError(cause)); }
    finally { setMoreLoading(false); }
  }

  const selected = models.find(item => item.model.id === selectedModelId) ?? null;
  // 모델 전환·재조회 중 과거 결과의 UI 상태가 새 모델 화면에 남지 않도록 한다.
  const focusedResult = selected !== null && resultFocusedModelId === selectedModelId;
  const modelTitle = (item: ModelOffering) => `Isolation Forest · v${item.model.registry_version ?? "?"}`;
  return <section className={`model-lab-use${focusedResult ? " result-focused" : ""}${showResultSetup ? " show-setup" : ""}`}
    aria-label="활동 이상 탐지 모델 적용">
    {focusedResult && <div className="model-lab-result-toolbar">
      <span>{selected ? modelTitle(selected) : "모델"} · Dataset 분석 결과</span>
      <button type="button" className="secondary" aria-expanded={showResultSetup}
        onClick={() => setShowResultSetup(value => !value)}>
        {showResultSetup ? "모델·데이터 선택 닫기" : "모델·데이터 선택 변경"}
      </button>
    </div>}
    <header className="model-lab-use-heading">
      <div>
        <h2 id="model-lab-use-heading">모델 적용</h2>
        <p>준비된 활동 이상 탐지 모델로 내 데이터의 이상 점수와 관측값을 조사하고, 입력값을 바꿔 모델의 반응을 살펴봅니다.</p>
      </div>
    </header>
    {loading && <p role="status">사용할 수 있는 모델을 확인하는 중입니다.</p>}
    {error && <div className="notice-blocked" role="alert">
      <p>{models.length > 0 ? "추가 모델을 불러오지 못했습니다: " : "모델 목록을 확인하지 못했습니다: "}{error}</p>
      <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}>다시 조회</button>
    </div>}
    {!loading && !error && models.length === 0 && <div className="model-lab-no-models">
      <h3>{cursor ? "호환되는 모델을 아직 찾지 못했습니다" : "준비된 활동 이상 탐지 모델이 없습니다"}</h3>
      <p>{cursor
        ? "제공된 모델이 더 있습니다. 다음 목록을 확인하면 사용할 모델이 나올 수 있습니다."
        : "모델이 준비되면 이곳에서 선택해 데이터에 적용할 수 있습니다. 데이터 탐색은 지금 이용할 수 있습니다."}</p>
    </div>}
    {selected && <div className="model-lab-current-model">
      <div><span className="eyebrow">현재 선택한 모델</span>
        <h3>{modelTitle(selected)}</h3>
        <p>학습 시각 {new Date(selected.model.created_at).toLocaleString("ko-KR")}
          · 입력 Feature {selected.model.features.length}개</p>
        <p className="model-offering-description">{selected.description}</p></div>
      {models.length > 1 && <div className="model-lab-model-actions">
        <details className="model-lab-version-picker">
          <summary>다른 모델 선택</summary>
          <div className="model-lab-model-list">
            {models.map(item => <button key={item.model.id} type="button"
              className="surface-button model-lab-model-choice" aria-pressed={selectedModelId === item.model.id}
              onClick={() => onSelectModel(item.model.id)}>
              <span><strong>{modelTitle(item)}</strong>
                <small>학습 {new Date(item.model.created_at).toLocaleString("ko-KR")}</small>
                <small>{item.description}</small></span>
              <span>{selectedModelId === item.model.id ? "선택됨" : "선택 →"}</span>
            </button>)}
          </div>
        </details>
      </div>}
    </div>}
    {!loading && cursor && (!focusedResult || showResultSetup) && <button type="button"
      className="secondary model-lab-more-versions" disabled={moreLoading}
      onClick={() => void loadMore()}>{moreLoading ? "모델 확인 중…" : "다음 모델 조회"}</button>}
    {!loading && !error && selectedModelId && !selected &&
      <div className="notice-blocked" role="status">
        <p>URL에서 선택한 모델은 현재 제공 중인 호환 모델 목록에 없습니다.
          다음 목록을 조회하거나 새로고침해 주세요.</p>
        <button type="button" className="secondary"
          onClick={() => onSelectModel(models[0]?.model.id ?? "")}>
          {models.length > 0 ? "제공 중인 모델 선택" : "선택 해제"}</button>
      </div>}
    {selected && <ModelApplicationWorkspace selectedDatasetId={selectedDatasetId} onSelectDataset={onSelectDataset} key={selected.model.id} model={selected.model}
      datasets={datasets} hasMoreDatasets={hasMoreDatasets} onLoadMoreDatasets={onLoadMoreDatasets}
      canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource}
      canUpload={canUpload} onDatasetRegistered={onDatasetRegistered} onResultViewChange={handleResultViewChange} />}
    <div className="model-lab-use-footer">
      <button type="button" className="ghost" onClick={onHistory}>실행 기록 →</button>
    </div>
  </section>;
}
