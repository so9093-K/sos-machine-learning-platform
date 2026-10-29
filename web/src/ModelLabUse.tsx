/** 제공된 모델 중 활동 이상 탐지 입력 계약에 맞는 판본을 사용자의 데이터에 적용한다. */
import { useCallback, useEffect, useState } from "react";
import { formatError, request } from "./api";
import { ModelApplicationWorkspace } from "./ModelApplicationWorkspace";
import { isActivityModelCompatible } from "./ModelLabTaxonomy";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelOffering, ModelOfferingPage, ModelOfferingState, ModelRevision, PipelineDefinition } from "./types";

type SelectedOffering = Pick<ModelOffering, "model" | "description">;

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
  const [selectionRefresh, setSelectionRefresh] = useState(0);
  const [directSelection, setDirectSelection] = useState<{
    id: string; value: SelectedOffering | null; error: string;
  } | null>(null);
  const [resultFocusedModelId, setResultFocusedModelId] = useState<string | null>(null);
  const [showResultSetup, setShowResultSetup] = useState(false);
  const handleResultViewChange = useCallback((visible: boolean) => {
    setResultFocusedModelId(visible ? selectedModelId : null);
  }, [selectedModelId]);
  const compatible = (page: ModelOfferingPage, current: PipelineDefinition) =>
    page.items.filter(item => isActivityModelCompatible(item.model, current));
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setDefinition(null); setModels([]); setCursor(null); setDirectSelection(null);
    void request<PipelineDefinition[]>("/api/pipelines")
      .then(async definitions => {
        const current = definitions.find(item => item.id === "activity-anomaly-if");
        if (!current) throw new Error("활동 이상 탐지 분석 방법을 찾을 수 없습니다.");
        if (!active) return;
        setDefinition(current);
        const page = await offeredPage(null);
        if (!active) return;
        setModels(compatible(page, current));
        setCursor(page.next_cursor ?? null);
      })
      .catch(cause => { if (active) setError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  useEffect(() => {
    if (!loading && !selectedModelId && models.length) onSelectModel(models[0].model.id);
  }, [loading, selectedModelId, models, onSelectModel]);

  const listedSelection = models.find(item => item.model.id === selectedModelId);
  useEffect(() => {
    if (loading || !definition || !selectedModelId || listedSelection) return;
    let active = true;
    setDirectSelection(null);
    const path = `/api/ml/models/${encodeURIComponent(selectedModelId)}`;
    // 명시된 판본은 목록 페이지 위치와 무관하게 서버의 제공 상태를 확인한다.
    void Promise.all([request<ModelRevision>(path), request<ModelOfferingState>(`${path}/offering`)])
      .then(([model, offering]) => {
        if (!offering.offered) throw new Error("선택한 모델은 현재 제공 중이 아닙니다.");
        if (model.lifecycle_state !== "Active" || !model.registry_version) {
          throw new Error("선택한 모델은 현재 적용 가능한 판본이 아닙니다.");
        }
        if (!isActivityModelCompatible(model, definition)) {
          throw new Error("선택한 모델의 입력 계약은 활동 이상 탐지와 호환되지 않습니다.");
        }
        if (active) setDirectSelection({ id: selectedModelId,
          value: { model, description: offering.description ?? "" }, error: "" });
      })
      .catch(cause => { if (active) setDirectSelection({ id: selectedModelId, value: null, error: formatError(cause) }); });
    return () => { active = false; };
  }, [loading, definition, selectedModelId, listedSelection, selectionRefresh]);

  async function loadMore() {
    if (!definition || !cursor || moreLoading) return;
    setMoreLoading(true); setError("");
    try {
      const page = await offeredPage(cursor);
      const items = compatible(page, definition);
      setModels(previous => [...previous, ...items.filter(item =>
        !previous.some(entry => entry.model.id === item.model.id))]);
      setCursor(page.next_cursor ?? null);
    } catch (cause) { setError(formatError(cause)); }
    finally { setMoreLoading(false); }
  }

  const direct = directSelection?.id === selectedModelId ? directSelection : null;
  const selected = loading ? null : listedSelection ?? direct?.value ?? null;
  const choices = direct?.value && !listedSelection ? [direct.value, ...models] : models;
  const checkingSelection = !loading && definition && selectedModelId && !selected && !direct;
  // 모델 전환·재조회 중 과거 결과의 UI 상태가 새 모델 화면에 남지 않도록 한다.
  const focusedResult = selected !== null && resultFocusedModelId === selectedModelId;
  const modelTitle = (item: SelectedOffering) => `Isolation Forest · v${item.model.registry_version ?? "?"}`;
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
    {!loading && !error && !selectedModelId && models.length === 0 && <div className="model-lab-no-models">
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
        <p className="model-offering-description">{selected.description}</p>
        {selectedDatasetId && <p className="hint">기본 모델 선택은 활동 이상 탐지의 Feature 계약을 기준으로 합니다.
          선택 Dataset의 적용 조건은 Dataset 분석의 입력 검사 결과에서 확인하세요.</p>}</div>
      {choices.length > 1 && <div className="model-lab-model-actions">
        <details className="model-lab-version-picker">
          <summary>다른 모델 선택</summary>
          <div className="model-lab-model-list">
            {choices.map(item => <button key={item.model.id} type="button"
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
    {checkingSelection && <p role="status">선택한 모델의 제공 상태와 호환성을 확인하는 중입니다.</p>}
    {!loading && !selected && direct?.error &&
      <div className="notice-blocked" role="alert">
        <p>선택한 모델 확인 실패 · {direct.error}</p>
        <button type="button" className="secondary" onClick={() => setSelectionRefresh(value => value + 1)}>
          선택 모델 다시 조회</button>
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
