/** 현재 Actor가 사용할 수 있는 Registry 모델 중 활동 이상 탐지 입력 계약에 맞는 모델만 사용한다. */
import { useCallback, useEffect, useRef, useState } from "react";
import { formatError, request } from "./api";
import { MLScoringPanel } from "./MLScoringPanel";
import { ModelLabPlayground, observationInputForPlayground } from "./ModelLabPlayground";
import type { ObservationExperiment } from "./ModelLabPlayground";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
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
  selectedModelId, selectedGroupKey, onSelectModel, onSelectGroup, onOpenResource, onBack, onLearn,
  canLearn, canUpload, onDatasetRegistered }: {
  datasets: Dataset[];
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  canRun: boolean; canCancel: boolean;
  selectedModelId: string;
  selectedGroupKey: string;
  onSelectModel: (id: string) => void;
  onSelectGroup: (group: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onBack: () => void; onLearn: () => void;
  canLearn: boolean; canUpload: boolean;
  onDatasetRegistered: (dataset: Dataset) => void;
}) {
  const [models, setModels] = useState<RegisteredActivityModel[]>([]);
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [groupError, setGroupError] = useState("");
  const registryKey = selectedGroupKey;
  const groupTitle = groups.find(group => group.registry_key === registryKey)?.title ??
    (registryKey === "default" ? "기본 활동 이상 탐지" : registryKey);
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [error, setError] = useState("");
  const [lookupFailures, setLookupFailures] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [usage, setUsage] = useState<"playground" | "dataset">("dataset");
  const [observationInput, setObservationInput] = useState<ObservationExperiment | null>(null);
  // 모델 판본이 바뀌면 앞서 선택한 다른 판본의 관측값 입력을 재사용하지 않는다.
  const observationRequest = useRef(0);
  const playgroundPanel = useRef<HTMLDivElement>(null);
  const datasetPanel = useRef<HTMLDivElement>(null);
  const [resultFocusedModelId, setResultFocusedModelId] = useState<string | null>(null);
  const [showResultSetup, setShowResultSetup] = useState(false);
  const handleResultViewChange = useCallback((visible: boolean) => {
    setResultFocusedModelId(visible ? selectedModelId : null);
  }, [selectedModelId]);
  useEffect(() => { setObservationInput(null); }, [selectedModelId]);
  const openDataset = () => setUsage("dataset");
  const backToObservation = () => {
    setUsage("dataset");
    window.requestAnimationFrame(() => {
      const detail = datasetPanel.current?.querySelector<HTMLElement>(".scoring-observation-detail");
      detail?.focus({ preventScroll: true });
      detail?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto" : "smooth", block: "start" });
    });
  };

  useEffect(() => {
    if (usage !== "playground" || !observationInput) return;
    const id = window.requestAnimationFrame(() => {
      const target = playgroundPanel.current?.querySelector<HTMLElement>(".model-lab-playground");
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto" : "smooth", block: "start",
      });
    });
    return () => window.cancelAnimationFrame(id);
  }, [usage, observationInput?.id]);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setGroupError(""); setGroups([]); setDefinition(null);
    setModels([]); setNextPage(null); setLookupFailures(0);
    // Registry 모델 조회 API가 소유권·보관 상태를 검증한다.
    // 모델 그룹 선택지는 보조 정보이므로 그 조회 장애로 유효한 모델 사용을 막지 않는다.
    void request<ModelGroupPage>("/api/ml/registry/groups")
      .then(page => { if (active) setGroups(page.items.filter(item => !item.archived)); })
      .catch(cause => { if (active) setGroupError(formatError(cause)); });
    void Promise.all([
      request<PipelineDefinition[]>("/api/pipelines"),
      request<MLflowModelVersionPage>("/api/ml/registry/models?registry_key=" +
        encodeURIComponent(registryKey)),
    ]).then(async ([definitions, page]) => {
      const current = definitions.find(item => item.id === "activity-anomaly-if");
      if (!current) throw new Error("활동 이상 탐지 분석 방법을 찾을 수 없습니다.");
      const checked = await compatiblePage(page, current);
      if (active) {
        setDefinition(current);
        setModels(checked.items);
        if (!selectedModelId && checked.items.length) onSelectModel(checked.items[0].revision.id);
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
  // 모델 전환·Registry 재조회 중 과거 결과의 UI 상태가 새 모델 화면에 남지 않도록 한다.
  const focusedResult = usage === "dataset" && selected !== null &&
    resultFocusedModelId === selectedModelId;
  function tryObservation(
    features: Record<string, number | null>, rowIndex: number, score: number,
    scoringResultId: string, modelRevisionId: string,
  ): string | null {
    // 모델·관측값 근거를 묵시적으로 교체하거나 누락 Feature를 중앙값으로 대체하지 않는다.
    if (!selected || selected.revision.id !== modelRevisionId)
      return "선택한 관측값과 모델 판본이 일치하지 않습니다. 모델을 다시 확인해 주세요.";
    const prepared = observationInputForPlayground(selected.revision, features);
    if (!prepared.fields) {
      const examples = prepared.invalid.slice(0, 3).map(activityFeatureLabel).join(", ");
      return `이 관측값은 직접 입력에 필요한 Feature ${prepared.invalid.length}개를 그대로 사용할 수 없습니다.` +
        ` ${examples}${prepared.invalid.length > 3 ? " 외" : ""} 값을 확인해 주세요.`;
    }
    setObservationInput({
      id: `${scoringResultId}:${rowIndex}:${++observationRequest.current}`,
      modelRevisionId, scoringResultId, rowIndex, originalScore: score, fields: prepared.fields,
    });
    setUsage("playground");
    return null;
  }
  // 모델 그룹 전환은 보조 설정이다. 선택한 모델이 있으면 모델 카드 안에, 없으면 단독으로 둔다.
  const groupOptions = <details className="model-lab-model-options">
    <summary>모델 그룹 관리 · 목록 새로고침</summary>
    {groupError && <p role="status" className="hint">
      다른 모델 그룹을 불러오지 못했습니다. 현재 선택한 모델 그룹의 사용 가능 여부는 별도로 확인합니다.
      <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}>
        그룹 다시 조회
      </button>
    </p>}
    <label>사용할 모델 그룹
      <select value={registryKey} onChange={event => {
        onSelectGroup(event.target.value);
      }}>
        <option value="default">{groups.find(group => group.registry_key === "default")?.title ?? "기본 활동 이상 탐지"}</option>
        {registryKey !== "default" && !groups.some(g => g.registry_key === registryKey) &&
          <option value={registryKey}>현재 선택한 모델 그룹</option>}
        {groups.filter(g => g.registry_key !== "default").map(g =>
          <option key={g.registry_key} value={g.registry_key}>{g.title}</option>)}
      </select>
    </label>
    <button type="button" className="secondary" onClick={() => setRefresh(value => value + 1)}
      disabled={loading || moreLoading}>모델 목록 새로고침</button>
  </details>;
  return <section className={`model-lab-use${focusedResult ? " result-focused" : ""}${showResultSetup ? " show-setup" : ""}`}
    aria-label="활동 이상 탐지 모델 사용">
    {focusedResult && <div className="model-lab-result-toolbar">
      <span>Isolation Forest · v{selected?.version.version ?? "?"} · Dataset 분석 결과</span>
      <button type="button" className="secondary" aria-expanded={showResultSetup}
        onClick={() => setShowResultSetup(value => !value)}>
        {showResultSetup ? "모델·데이터 선택 닫기" : "모델·데이터 선택 변경"}
      </button>
    </div>}
    <button type="button" className="ghost model-lab-back" onClick={onBack}>
      ← 활동 이상 탐지 소개로
    </button>
    <header className="model-lab-use-heading">
      <div>
        <h2 id="model-lab-use-heading">모델 적용</h2>
        <p>데이터를 분석하고, 궁금한 관측값은 직접 바꿔 모델의 반응을 살펴봅니다.</p>
      </div>
    </header>
    {!selected && groupOptions}
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
          : canLearn
            ? "이 그룹에는 사용할 수 있는 모델이 없습니다. 다른 모델 그룹을 확인하거나 새 모델을 학습해 주세요."
            : "이 그룹에는 사용할 수 있는 모델이 없습니다. 현재 계정에는 모델 학습(Job 실행) 권한이 없어, 학습 권한이 있는 사용자가 모델을 등록하면 이곳에서 적용할 수 있습니다."}</p>
      {!nextPage && !lookupFailures && groups.some(group => group.registry_key !== registryKey) &&
        <button type="button" className="secondary"
          onClick={() => onSelectGroup(groups.find(group => group.registry_key !== registryKey)!.registry_key)}>
          다른 모델 그룹 확인 →
        </button>}
      {!nextPage && !lookupFailures && canLearn &&
        <button type="button" onClick={onLearn}>모델 학습 →</button>}
    </div>}
    {selected && <div className="model-lab-current-model">
      <div><span className="eyebrow">현재 선택한 모델</span>
        <h3>{groupTitle} · v{selected.version.version}</h3>
        <p>Isolation Forest · 학습 시각 {new Date(selected.revision.created_at).toLocaleString("ko-KR")}
          · 입력 Feature {selected.revision.features.length}개</p></div>
      <div className="model-lab-model-actions">
      <details className="model-lab-version-picker">
        <summary>다른 모델 버전 선택</summary>
        <div className="model-lab-model-list">
          {models.map(({ revision, version }) => <button key={revision.id} type="button"
            className="surface-button model-lab-model-choice" aria-pressed={selectedModelId === revision.id}
            onClick={() => onSelectModel(revision.id)}>
            <span><strong>{groupTitle} · v{version.version}</strong>
              <small>학습 {new Date(revision.created_at).toLocaleString("ko-KR")}</small>
              <small>{datasets.find(item => item.id === revision.dataset_id)?.name ??
                "현재 목록 외의 Dataset"} · Feature {revision.features.length}개</small></span>
            <span>{selectedModelId === revision.id ? "선택됨" : "선택 →"}</span>
          </button>)}
        </div>
      </details>
      {groupOptions}
      </div>
    </div>}
    {!loading && nextPage && (!focusedResult || showResultSetup) && <button type="button" className="secondary model-lab-more-versions" disabled={moreLoading}
      onClick={() => void loadMore()}>{moreLoading ? "모델 확인 중…" : "다음 모델 버전 조회"}</button>}
    {!loading && !error && selectedModelId && !selected &&
      <div className="pipeline-readiness-blocked" role="status">
        <p>URL에서 선택한 모델은 현재 조회된 호환 모델 목록에 없습니다.
          다음 모델 판본을 조회하거나 목록을 새로고침해 주세요.</p>
        <button type="button" className="secondary"
          onClick={() => onSelectModel(models[0]?.revision.id ?? "")}>
          {models.length > 0 ? "조회된 모델 적용" : "선택 해제"}</button>
      </div>}
    {!loading && !selected && !selectedModelId && models.length > 0 &&
      <div className="pipeline-readiness-blocked">
        <p>사용 가능한 모델이 확인됐습니다. 이 모델을 바로 적용할 수 있습니다.</p>
        <button type="button" className="secondary"
          onClick={() => onSelectModel(models[0].revision.id)}>이 모델 적용 →</button>
      </div>}
    {selected && <>
      <div className="model-lab-start-choice">
        <span className="model-lab-start-label" id="model-lab-start-label">분석할 데이터</span>
        <div className="model-lab-usage-tabs" role="group" aria-labelledby="model-lab-start-label">
          <button type="button" className="secondary" aria-pressed={usage === "dataset"}
            onClick={openDataset}>Dataset 분석</button>
          <button type="button" className="secondary" aria-pressed={usage === "playground"}
            onClick={() => setUsage("playground")}>값 직접 입력</button>
        </div>
        <p className="hint">{usage === "dataset"
          ? "등록된 Dataset이나 새 파일 전체에 모델을 적용합니다."
          : "관측값 한 건의 Feature를 입력해 모델 반응을 확인합니다."} 실행 버튼을 누를 때에만 계산합니다.</p>
      </div>
      <div hidden={usage !== "playground"} ref={playgroundPanel}>
        <ModelLabPlayground key={selected.revision.id} model={selected.revision}
          canRun={canRun} canCancel={canCancel}
          observationInput={observationInput?.modelRevisionId === selected.revision.id
            ? observationInput : null}
          onBackToAnalysis={backToObservation} />
      </div>
      <div hidden={usage !== "dataset"} ref={datasetPanel}>
        <MLScoringPanel key={selected.revision.id} model={selected.revision}
          datasets={datasets} hasMoreDatasets={hasMoreDatasets}
          onLoadMoreDatasets={onLoadMoreDatasets} canRun={canRun} canCancel={canCancel}
          onOpenResult={id => onOpenResource("scoring", id)}
          onOpenResource={onOpenResource}
          canUpload={canUpload} onDatasetRegistered={onDatasetRegistered}
          onResultViewChange={handleResultViewChange} onTryObservation={tryObservation} />
      </div>
    </>}
    {canLearn && <div className="model-lab-use-footer">
      <button type="button" className="ghost" onClick={onLearn}>새 모델 학습 →</button>
    </div>}
  </section>;
}
