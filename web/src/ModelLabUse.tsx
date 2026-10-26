/** 현재 Actor가 사용할 수 있는 Registry 모델 중 활동 이상 탐지 입력 계약에 맞는 모델만 사용한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { MLScoringPanel } from "./MLScoringPanel";
import { ModelLabPlayground } from "./ModelLabPlayground";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, MLflowModelVersion, MLflowModelVersionPage, ModelRevision, PipelineDefinition } from "./types";

type RegisteredActivityModel = { version: MLflowModelVersion; revision: ModelRevision };
type ModelGroup = { registry_key: string; title: string; archived: boolean };
type ModelGroupPage = { items: ModelGroup[]; next_page_token: string | null };

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
      .filter(({ revision }) => isActivityModelCompatible(revision, definition))
      .sort((a, b) => Date.parse(b.revision.created_at) - Date.parse(a.revision.created_at)),
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
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [registryKey, setRegistryKey] = useState("default");
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [error, setError] = useState("");
  const [lookupFailures, setLookupFailures] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [usage, setUsage] = useState<"playground" | "dataset">("playground");
  const [datasetVisited, setDatasetVisited] = useState(false);
  const openDataset = () => { setDatasetVisited(true); setUsage("dataset"); };

  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setDefinition(null);
    setModels([]); setNextPage(null); setLookupFailures(0);
    void Promise.all([
      request<PipelineDefinition[]>("/api/pipelines"),
      request<MLflowModelVersionPage>("/api/ml/registry/models?registry_key=" +
        encodeURIComponent(registryKey)),
      request<ModelGroupPage>("/api/ml/registry/groups"),
    ]).then(async ([definitions, page, groupsPage]) => {
      const current = definitions.find(item => item.id === "activity-anomaly-if");
      if (!current) throw new Error("활동 이상 탐지 분석 방법을 찾을 수 없습니다.");
      const checked = await compatiblePage(page, current);
      if (active) {
        setDefinition(current);
        setGroups(groupsPage.items.filter(item => !item.archived));
        const groupArchived = groupsPage.items.some(item =>
          item.registry_key === registryKey && item.archived);
        setModels(groupArchived ? [] : checked.items);
        if (!groupArchived && !selectedModelId && checked.items.length) onSelectModel(checked.items[0].revision.id);
        setLookupFailures(checked.failed);
        setNextPage(page.next_page_token ?? null);
      }
    }).catch(cause => { if (active) setError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh, registryKey]);

  async function loadMore() {
    if (!definition || !nextPage || moreLoading) return;
    const token = nextPage;
    setMoreLoading(true); setError("");
    try {
      const page = await request<MLflowModelVersionPage>(
        "/api/ml/registry/models?registry_key=" + encodeURIComponent(registryKey) +
        "&page_token=" + encodeURIComponent(token));
      const checked = await compatiblePage(page, definition);
      setModels(previous => {
        const seen = new Set(previous.map(item => item.revision.id));
        return [...previous, ...checked.items.filter(item => !seen.has(item.revision.id))];
      });
      setLookupFailures(previous => previous + checked.failed);
      setNextPage(page.next_page_token ?? null);
      // 첫 Registry 페이지에 호환 모델이 없던 경우도 시작 지점을 만든다.
      if (!selectedModelId && checked.items.length) onSelectModel(checked.items[0].revision.id);
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
        <h2 id="model-lab-use-heading">준비된 모델로 바로 분석하기</h2>
        <p>한 건의 활동을 체험하거나 Dataset 전체의 이상 점수를 계산합니다.
          모델 관리 없이 실제 결과부터 확인할 수 있습니다.</p>
      </div>
    </header>
    <details className="model-lab-model-options">
      <summary>사용 모델 변경 · 모델 목록 관리</summary>
      <label>사용할 모델 그룹
        <select value={registryKey} onChange={event => {
          setRegistryKey(event.target.value);
          onSelectModel("");
        }}>
          <option value="default">기본 활동 이상 탐지</option>
          {groups.filter(g => g.registry_key !== "default").map(g =>
            <option key={g.registry_key} value={g.registry_key}>{g.title}</option>)}
        </select>
      </label>
      <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}
        disabled={loading || moreLoading}>모델 목록 새로고침</button>
    </details>
    {loading && <p role="status">사용 가능한 모델을 확인하는 중입니다.</p>}
    {error && <div className="pipeline-readiness-blocked" role="alert">
      <p>{models.length > 0 || nextPage
        ? "추가 모델 버전을 불러오지 못했습니다: "
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
      <h3>{nextPage || lookupFailures
        ? "호환되는 모델을 아직 찾지 못했습니다"
        : "선택한 그룹에 사용할 수 있는 활동 이상 탐지 모델이 없습니다"}</h3>
      <p>{nextPage
        ? "등록된 모델 버전이 더 있습니다. 다음 페이지를 확인하면 사용할 모델이 나올 수 있습니다."
        : lookupFailures
          ? "일부 모델 정보를 확인하지 못해 사용 가능한 모델이 없는지 판단할 수 없습니다. 다시 조회해 주세요."
          : "처음 시작한다면 예제 데이터로 모델을 학습해 보세요. 등록이 끝나면 여기서 사용할 수 있습니다."}</p>
      {!nextPage && !lookupFailures &&
        <button type="button" onClick={onLearn}>예제로 모델 학습하기 →</button>}
    </div>}
    {selected && <div className="model-lab-current-model">
      <div><span className="eyebrow">현재 선택한 모델</span>
        <h3>Isolation Forest · v{selected.version.version}</h3>
        <p>학습 시각 {new Date(selected.revision.created_at).toLocaleString("ko-KR")}
          · 입력 Feature {selected.revision.features.length}개</p></div>
      <details className="model-lab-version-picker">
        <summary>사용할 모델 선택</summary>
        <div className="model-lab-model-list">
          {models.map(({ revision, version }) => <button key={revision.id} type="button"
            className="surface-button model-lab-model-choice" aria-pressed={selectedModelId === revision.id}
            onClick={() => onSelectModel(revision.id)}>
            <span><strong>Isolation Forest · v{version.version}</strong>
              <small>학습 {new Date(revision.created_at).toLocaleString("ko-KR")}</small>
              <small>{datasets.find(item => item.id === revision.dataset_id)?.name ??
                "현재 목록 외의 Dataset"} · Feature {revision.features.length}개</small></span>
            <span>{selectedModelId === revision.id ? "선택됨" : "선택 →"}</span>
          </button>)}
        </div>
      </details>
    </div>}
    {!loading && nextPage && <button type="button" className="secondary" disabled={moreLoading}
      onClick={() => void loadMore()}>{moreLoading ? "모델 확인 중…" : "다음 모델 버전 조회"}</button>}
    {!loading && !error && selectedModelId && !selected &&
      <div className="pipeline-readiness-blocked" role="status">
        <p>URL에서 선택한 모델은 현재 조회된 호환 모델 목록에 없습니다.
          다음 모델 판본을 조회하거나 목록을 새로고침해 주세요.</p>
        <button type="button" className="secondary"
          onClick={() => onSelectModel(models[0]?.revision.id ?? "")}>
          {models.length > 0 ? "조회된 모델 사용하기" : "선택 해제"}</button>
      </div>}
    {!loading && !selected && !selectedModelId && models.length > 0 &&
      <div className="pipeline-readiness-blocked">
        <p>사용 가능한 모델이 확인됐습니다. 이 모델로 바로 체험할 수 있습니다.</p>
        <button type="button" className="secondary"
          onClick={() => onSelectModel(models[0].revision.id)}>모델 사용하기 →</button>
      </div>}
    {selected && <>
      <p className="hint">준비된 모델을 선택했습니다. 아래에서 입력 방식을 선택하면 바로 실행할 수 있습니다.</p>
      <div className="model-lab-usage-tabs" role="group" aria-label="모델 적용 방식">
        <button type="button" className="secondary" aria-pressed={usage === "playground"}
          onClick={() => setUsage("playground")}>예시·직접 입력</button>
        <button type="button" className="secondary" aria-pressed={usage === "dataset"}
          onClick={openDataset}>Dataset 전체 적용</button>
      </div>
      <div hidden={usage !== "playground"}>
        <ModelLabPlayground key={selected.revision.id} model={selected.revision}
          canRun={canRun} canCancel={canCancel} onDataset={openDataset} />
      </div>
      <div hidden={usage !== "dataset"}>
        {datasetVisited && <MLScoringPanel key={selected.revision.id} model={selected.revision}
          datasets={datasets} hasMoreDatasets={hasMoreDatasets}
          onLoadMoreDatasets={onLoadMoreDatasets} canRun={canRun} canCancel={canCancel}
          onOpenResult={id => onOpenResource("scoring", id)}
          onOpenResource={onOpenResource} />}
      </div>
    </>}
    <div className="model-lab-use-footer">
      <button type="button" className="ghost" onClick={onLearn}>새 모델 학습해보기 →</button>
    </div>
  </section>;
}
