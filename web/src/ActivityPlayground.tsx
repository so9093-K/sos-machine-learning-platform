import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { activityFeatureGroups } from "./ActivityFeatureGroups";
import { ModelApplicationWorkspace } from "./ModelApplicationWorkspace";
import { ModelApiUsage } from "./ModelApiUsage";
import type { PlaygroundRun } from "./ModelLabPlayground";
import { isActivityModelCompatible } from "./ModelLabTaxonomy";
import { taskGuides, topicGuides, topicTree, TaxonomyTrail, usePipelineCatalog,
  PLAYGROUND_TOPICS, type TaxonomyLinks } from "./ModelLabGuide";
import { algorithmLabel, formatRecordedAt } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelOffering, ModelOfferingPage } from "./types";

const TOPIC = "activity_anomaly";
const EXAMPLE_ID = "activity-anomaly-if";

/** 활동 이상 탐지 Playground. 쓸모를 먼저 보이고, 같은 화면에서 실행·결과 확인·API 연결까지 잇는다.
 *
 *  모델 공급·학습 같은 운영 정보는 이 화면의 흐름에 두지 않는다.
 */
export function ActivityPlayground({ datasets, selectedDatasetId, onSelectDataset, hasMoreDatasets,
  onLoadMoreDatasets, onDatasetRegistered, canRun, canCancel, canUpload, onOpenResource, links }: {
  datasets: Dataset[];
  selectedDatasetId?: string;
  onSelectDataset: (id: string) => void;
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  onDatasetRegistered: (dataset: Dataset) => void;
  canRun: boolean; canCancel: boolean; canUpload: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  links: TaxonomyLinks;
}) {
  const catalog = usePipelineCatalog();
  const definition = catalog.items?.find(item => item.id === PLAYGROUND_TOPICS[TOPIC]) ?? null;
  const trail = topicTree(catalog.tree, TOPIC);
  const [offered, setOffered] = useState<ModelOffering[] | null>(null);
  const [offeredError, setOfferedError] = useState("");
  const [retry, setRetry] = useState(0);
  const [modelId, setModelId] = useState("");
  // 입력 구조가 확인된 Dataset만 API 예시에 채운다. 맞지 않는 ID로 실패하는 예시를 만들지 않는다.
  const [readyDatasetId, setReadyDatasetId] = useState("");
  // 단일 입력 예시는 Playground에서 실제로 계산한 마지막 입력을 그대로 재현한다.
  const [lastRun, setLastRun] = useState<PlaygroundRun | null>(null);
  const [example, setExample] = useState<{ busy: boolean; error: string; datasetId: string }>(
    { busy: false, error: "", datasetId: "" });
  useEffect(() => {
    let active = true;
    setOfferedError("");
    void request<ModelOfferingPage>("/api/ml/offered-models?limit=50")
      .then(page => { if (active) setOffered(page.items); })
      .catch(cause => { if (active) setOfferedError(formatError(cause)); });
    return () => { active = false; };
  }, [retry]);
  const models = definition && offered
    ? offered.filter(item => isActivityModelCompatible(item.model, definition)) : [];
  const chosen = models.find(item => item.model.id === modelId) ?? models[0] ?? null;
  const guide = topicGuides[TOPIC];

  async function useExample() {
    setExample(state => ({ ...state, busy: true, error: "" }));
    try {
      const dataset = await request<Dataset>(`/api/examples/${EXAMPLE_ID}/use`, { method: "POST" });
      onDatasetRegistered(dataset);
      onSelectDataset(dataset.id);
      setExample({ busy: false, error: "", datasetId: dataset.id });
    } catch (cause) {
      setExample(state => ({ ...state, busy: false, error: formatError(cause) }));
    }
  }

  return <div className="playground-page">
    {trail && <TaxonomyTrail learningType={trail.learningType} task={trail.task} topic={TOPIC} links={links} />}
    <section className="guide-section" aria-labelledby="playground-uses">
      <h2 id="playground-uses">활용 방법</h2>
      <div className="guide-cards">{guide.uses.map(item => <article key={item.title} className="guide-card">
        <h3>{item.title}</h3><p>{item.text}</p>
      </article>)}</div>
    </section>

    <section className="panel playground-try" aria-labelledby="playground-try-heading">
      <div className="panel-head"><div>
        <h2 id="playground-try-heading">직접 체험</h2>
        <p>준비된 모델을 활동 데이터나 직접 입력한 값에 적용하고, 점수가 높은 관측부터 무엇이 달랐는지 살펴봅니다.</p>
      </div></div>
      {(catalog.error || offeredError) && <div role="alert" className="guide-state">
        <p>모델을 불러오지 못했습니다. {catalog.error || offeredError}</p>
        <button type="button" className="secondary" onClick={() => { catalog.reload(); setRetry(value => value + 1); }}>
          다시 조회</button></div>}
      {!catalog.error && !offeredError && (offered === null || catalog.items === null) &&
        <p role="status" className="muted">사용할 수 있는 모델을 확인하는 중입니다.</p>}
      {offered !== null && definition && models.length === 0 && <p className="muted">
        아직 사용할 수 있는 활동 이상 탐지 모델이 없습니다. 모델이 준비되면 이곳에서 바로 적용할 수 있습니다.</p>}
      {chosen && <>
        <div className="playground-model">
          <span className="eyebrow">사용 모델</span>
          {models.length > 1
            ? <select aria-label="사용할 모델" value={chosen.model.id} onChange={event => setModelId(event.target.value)}>
              {models.map(item => <option key={item.model.id} value={item.model.id}>
                {algorithmLabel(item.model.algorithm)} · 제공 {formatRecordedAt(item.offered_at)}</option>)}
            </select>
            : <strong>{algorithmLabel(chosen.model.algorithm)}</strong>}
          <button type="button" className="ghost" onClick={() => onOpenResource("model", chosen.model.id)}>모델 상세 →</button>
          <p className="muted model-offering-description">{chosen.description}</p>
        </div>
        {!canRun && <div className="playground-notice" role="status">
          <strong>조회 전용 계정</strong>
          <p>점수 계산은 작업 실행 권한이 있는 계정에서 할 수 있습니다. 이 화면의 활용 방법·결과 해석·서비스 연동 방법은 그대로 확인할 수 있습니다.</p>
        </div>}
        {canRun && canUpload && example.datasetId !== selectedDatasetId && <div className="playground-example">
          <button type="button" className="secondary" disabled={example.busy} onClick={() => void useExample()}>
            {example.busy ? "예제 준비 중…" : "예제 데이터로 시작"}</button>
          <span className="hint">합성 예제를 내 Dataset으로 등록해 선택합니다. 이미 등록했다면 그대로 다시 씁니다.</span>
          {example.error && <p role="alert">예제를 준비하지 못했습니다. {example.error}</p>}
        </div>}
        {canRun && <ModelApplicationWorkspace key={chosen.model.id} model={chosen.model} datasets={datasets}
          selectedDatasetId={selectedDatasetId} onSelectDataset={onSelectDataset}
          hasMoreDatasets={hasMoreDatasets} onLoadMoreDatasets={onLoadMoreDatasets}
          canUpload={canUpload} onDatasetRegistered={onDatasetRegistered}
          canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource}
          onInputReadyChange={(datasetId, ready) => setReadyDatasetId(ready ? datasetId : "")}
          onPlaygroundRun={setLastRun} />}
      </>}
    </section>

    {taskGuides.anomaly_detection.reading && <section className="guide-section" aria-labelledby="playground-reading">
      <h2 id="playground-reading">결과 해석</h2>
      <div className="guide-cards">{taskGuides.anomaly_detection.reading.map(item =>
        <article key={item.title} className="guide-card"><h3>{item.title}</h3><p>{item.text}</p></article>)}</div>
    </section>}

    {chosen && <ModelApiUsage model={chosen.model}
      lastRun={lastRun?.modelRevisionId === chosen.model.id ? lastRun : undefined}
      datasetId={selectedDatasetId && readyDatasetId === selectedDatasetId ? selectedDatasetId : undefined} />}

    {definition && <details className="panel playground-details">
      <summary>입력 조건 · 모델 정보</summary>
      <p>모델은 사용자·호스트 단위로 집계한 숫자형 활동 Feature {definition.required_features.length}개를 이름과 자료형 그대로 요구합니다.
        컬럼 순서는 달라도 되며, 원시 로그에서 Feature를 자동으로 만들지 않습니다. 관측 시각 컬럼은 선택입니다.</p>
      {[...activityFeatureGroups.map(group => ({ id: group.id, title: group.title,
        names: definition.required_features.filter(name => (group.features as readonly string[]).includes(name)) })),
        { id: "other", title: "그 밖의 Feature", names: definition.required_features.filter(name =>
          !activityFeatureGroups.some(group => (group.features as readonly string[]).includes(name))) }]
        .filter(group => group.names.length > 0).map(group => <div key={group.id} className="playground-feature-group">
          <strong>{group.title} · {group.names.length}개</strong>
          <p>{group.names.map(activityFeatureLabel).join(" · ")}</p>
        </div>)}
      {chosen && <p className="hint">{algorithmLabel(chosen.model.algorithm)} · Registry v{chosen.model.registry_version ?? "?"}
        · 학습 {formatRecordedAt(chosen.model.created_at)} · 판본 {chosen.model.id}</p>}
    </details>}
  </div>;
}
