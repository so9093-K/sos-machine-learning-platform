import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { activityFeatureGroups } from "./ActivityFeatureGroups";
import { ModelApplicationWorkspace } from "./ModelApplicationWorkspace";
import { ModelApiUsage, servesModel } from "./ModelApiUsage";
import type { PlaygroundRun } from "./ModelLabPlayground";
import { canApplyModel, useModelOffering } from "./ModelOffering";
import { isTopicCompatible } from "./ModelLabTaxonomy";
import { taskGuides, topicGuides, topicTree, TaxonomyTrail, useTopicCatalog,
  type TaxonomyLinks } from "./ModelLabGuide";
import { algorithmLabel, formatRecordedAt } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelOffering, ModelOfferingPage, ModelRevision, ServingEndpoint } from "./types";

type Choice = { model: ModelRevision; description: string; offered: boolean };
type Selection =
  | { state: "ready"; choice: Choice }
  | { state: "loading" }
  | { state: "blocked"; reason: string }
  | { state: "none" };

const TOPIC = "activity_anomaly";
const EXAMPLE_ID = "activity-anomaly-if";
const SERVING_RECHECK_MS = 5000;

/** 이 판본을 서빙하거나 이 판본으로 전환 중인 Endpoint. 이미 요청을 처리하는 쪽을 먼저 고른다.
 *  이 판본의 배포가 실패한 전환은 준비 중으로 보이지 않게 제외한다. */
function servingFor(items: ServingEndpoint[], modelId: string): ServingEndpoint | null {
  const failed = (item: ServingEndpoint) => item.active_model_revision_id !== modelId &&
    ["DEPLOY_FAILED", "UNHEALTHY"].includes(item.runtime.pending?.status ?? "");
  const candidates = items.filter(item => item.desired_state === "serving" && !failed(item) &&
    (item.active_model_revision_id === modelId || item.pending_model_revision_id === modelId));
  return candidates.find(item => servesModel(item, modelId)) ?? candidates[0] ?? null;
}

/** 활동 이상 탐지 Playground. 쓸모를 먼저 보이고, 같은 화면에서 실행·결과 확인·API 연결까지 잇는다.
 *
 *  모델 공급·학습 같은 운영 정보는 이 화면의 흐름에 두지 않는다.
 */
export function ActivityPlayground({ datasets, selectedDatasetId, preferredModelId, onSelectModel, isAdministrator,
  onSelectDataset, hasMoreDatasets,
  onLoadMoreDatasets, onDatasetRegistered, canRun, canCancel, canUpload, canInfer, onOpenResource, links }: {
  datasets: Dataset[];
  selectedDatasetId?: string;
  /** 모델 목록·상세에서 고른 모델. 주소에 남아 새로고침·뒤로 가기에서도 유지된다. */
  preferredModelId?: string;
  onSelectModel: (id: string) => void;
  isAdministrator: boolean;
  onSelectDataset: (id: string) => void;
  hasMoreDatasets: boolean;
  onLoadMoreDatasets: () => Promise<void>;
  onDatasetRegistered: (dataset: Dataset) => void;
  canRun: boolean; canCancel: boolean; canUpload: boolean; canInfer: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  links: TaxonomyLinks;
}) {
  const catalog = useTopicCatalog();
  // 주제의 입력·출력 계약. 어떤 알고리즘·Pipeline으로 만든 모델이든 이 계약을 따르면 체험할 수 있다.
  const topic = catalog.items?.find(item => item.id === TOPIC) ?? null;
  const trail = topicTree(catalog.tree, TOPIC);
  const [offered, setOffered] = useState<ModelOffering[] | null>(null);
  const [offeredError, setOfferedError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [requestCursor, setRequestCursor] = useState<string | null>(null);
  const [loadingModels, setLoadingModels] = useState(true);
  const [retry, setRetry] = useState(0);
  // 입력 구조가 확인된 Dataset만 API 예시에 채운다. 맞지 않는 ID로 실패하는 예시를 만들지 않는다.
  const [readyDatasetId, setReadyDatasetId] = useState("");
  // 단일 입력 예시는 Playground에서 실제로 계산한 마지막 입력을 그대로 재현한다.
  const [lastRun, setLastRun] = useState<PlaygroundRun | null>(null);
  const [example, setExample] = useState<{ busy: boolean; error: string; datasetId: string }>(
    { busy: false, error: "", datasetId: "" });
  useEffect(() => {
    let active = true;
    setOfferedError("");
    setLoadingModels(true);
    void request<ModelOfferingPage>("/api/ml/offered-models?limit=50" +
      (requestCursor ? "&cursor=" + encodeURIComponent(requestCursor) : ""))
      .then(page => { if (active) {
        setOffered(previous => requestCursor
          ? [...new Map([...(previous ?? []), ...page.items].map(item => [item.model.id, item])).values()]
          : page.items);
        setNextCursor(page.next_cursor ?? null);
      } })
      .catch(cause => { if (active) setOfferedError(formatError(cause)); })
      .finally(() => { if (active) setLoadingModels(false); });
    return () => { active = false; };
  }, [retry, requestCursor]);
  const models = topic && offered
    ? offered.filter(item => isTopicCompatible(item.model, topic)) : [];
  // 지정한 모델이 첫 목록 페이지에 없으면 그 판본을 직접 확인한다. 다른 모델로 대체하지 않는다.
  const listed = preferredModelId ? models.find(item => item.model.id === preferredModelId) ?? null : null;
  const lookupId = preferredModelId && topic && offered !== null && !listed ? preferredModelId : null;
  const [direct, setDirect] = useState<{ id: string; model: ModelRevision | null; error: string } | null>(null);
  useEffect(() => {
    if (!lookupId) return;
    let active = true;
    setDirect(null);
    void request<ModelRevision>(`/api/ml/models/${encodeURIComponent(lookupId)}`)
      .then(model => { if (active) setDirect({ id: lookupId, model, error: "" }); })
      .catch(cause => { if (active) setDirect({ id: lookupId, model: null, error: formatError(cause) }); });
    return () => { active = false; };
  }, [lookupId]);
  const directOffering = useModelOffering(lookupId);
  const selection = ((): Selection => {
    if (!preferredModelId) return models[0]
      ? { state: "ready", choice: { model: models[0].model, description: models[0].description, offered: true } }
      : { state: "none" };
    if (listed) return { state: "ready", choice: { model: listed.model, description: listed.description, offered: true } };
    if (!lookupId || direct?.id !== lookupId) return { state: "loading" };
    if (direct.error || !direct.model) return { state: "blocked", reason: `선택한 모델을 열 수 없습니다. ${direct.error}` };
    if (topic && !isTopicCompatible(direct.model, topic)) return { state: "blocked",
      reason: "선택한 모델은 활동 이상 탐지의 입력 조건(Feature)이나 출력 형식(이상 점수)과 맞지 않습니다. 모델 상세에서 입력 조건을 확인하세요." };
    const state = directOffering.offering;
    if (directOffering.error && !state) return { state: "blocked",
      reason: `선택한 모델의 제공 상태를 확인하지 못했습니다. ${directOffering.error}` };
    if (!state) return { state: "loading" };
    if (!canApplyModel(direct.model, state, isAdministrator)) return { state: "blocked",
      reason: direct.model.lifecycle_state !== "Active" || !direct.model.registry_version
        ? "선택한 모델은 현재 새 계산에 사용할 수 없습니다."
        : "선택한 모델은 제공이 중단됐습니다. 이미 만든 결과는 확인할 수 있지만 새 적용은 할 수 없습니다." };
    return { state: "ready", choice: { model: direct.model, description: state.description ?? "", offered: state.offered } };
  })();
  const chosen = selection.state === "ready" ? selection.choice : null;
  // 선택한 판본의 온라인 API. 준비 중이면 준비될 때까지 다시 확인하고, 없거나 확인하지 못하면
  // 사용자가 다시 확인할 수 있다.
  const [serving, setServing] = useState<{ modelId: string; endpoint: ServingEndpoint | null; error: string } | null>(null);
  const [servingCheck, setServingCheck] = useState(0);
  const chosenId = chosen?.model.id ?? "";
  useEffect(() => {
    if (!chosenId) return;
    let active = true;
    void request<ServingEndpoint[]>(`/api/serving/endpoints?model_revision_id=${encodeURIComponent(chosenId)}`)
      .then(items => { if (active) setServing({ modelId: chosenId, endpoint: servingFor(items, chosenId), error: "" }); })
      .catch(cause => { if (active) setServing({ modelId: chosenId, endpoint: null, error: formatError(cause) }); });
    return () => { active = false; };
  }, [chosenId, servingCheck]);
  const current = serving?.modelId === chosenId ? serving : null;
  const endpoint = current?.endpoint ?? null;
  const onlineReady = servesModel(endpoint, chosenId);
  const preparing = Boolean(endpoint) && !onlineReady;
  useEffect(() => {
    if (!preparing) return;
    const timer = window.setTimeout(() => setServingCheck(value => value + 1), SERVING_RECHECK_MS);
    return () => window.clearTimeout(timer);
  }, [preparing, serving]);
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

    {/* 적용 화면이 자체 카드이므로 바깥은 카드로 감싸지 않는다. */}
    <section className="guide-section playground-try" aria-labelledby="playground-try-heading">
      <h2 id="playground-try-heading">직접 체험</h2>
      <p className="muted">준비된 모델을 활동 데이터나 직접 입력한 값에 적용하고, 점수가 높은 관측부터 무엇이 달랐는지 살펴봅니다.</p>
      {(catalog.error || offeredError) && <div role="alert" className="guide-state">
        <p>모델을 불러오지 못했습니다. {catalog.error || offeredError}</p>
        <button type="button" className="secondary" onClick={() => { catalog.reload(); setRetry(value => value + 1); }}>
          다시 조회</button></div>}
      {!catalog.error && !offeredError && (offered === null || catalog.items === null) &&
        <p role="status" className="muted">사용할 수 있는 모델을 확인하는 중입니다.</p>}
      {selection.state === "loading" && preferredModelId && offered !== null && topic &&
        <p role="status" className="muted">선택한 모델을 확인하는 중입니다.</p>}
      {selection.state === "blocked" && <div className="playground-notice" role="alert">
        <strong>선택한 모델을 사용할 수 없습니다</strong>
        <p>{selection.reason}</p>
        {models.length > 0 && <button type="button" className="secondary" onClick={() => onSelectModel("")}>
          제공 모델 중에서 다시 선택</button>}
      </div>}
      {selection.state === "none" && offered !== null && topic && <div className="playground-notice">
        <p className="muted">
        {nextCursor ? "현재 조회한 목록에 호환 모델이 없습니다. 다음 제공 모델을 조회할 수 있습니다."
          : "첫 점수 계산에는 활동 이상 탐지용 제공 모델이 필요합니다. 개발·배포 담당자가 체험용 합성 예제 모델을 준비할 수 있습니다."}</p>
        {!nextCursor && <button type="button" className="secondary" disabled={loadingModels}
          onClick={() => { setRequestCursor(null); setRetry(value => value + 1); }}>제공 모델 다시 조회</button>}
      </div>}
      {nextCursor && <button type="button" className="secondary" disabled={loadingModels}
        onClick={() => {
          if (requestCursor === nextCursor) setRetry(value => value + 1);
          else setRequestCursor(nextCursor);
        }}>{loadingModels ? "모델 조회 중…" : "다음 제공 모델 조회"}</button>}
      {chosen && <>
        <div className="playground-model">
          <span className="eyebrow">사용 모델</span>
          {models.length > 1 || (models.length > 0 && !listed && preferredModelId)
            ? <select aria-label="사용할 모델" value={chosen.model.id} onChange={event => onSelectModel(event.target.value)}>
              {!models.some(item => item.model.id === chosen.model.id) && <option value={chosen.model.id}>
                {algorithmLabel(chosen.model.algorithm)} · 선택한 모델</option>}
              {models.map(item => <option key={item.model.id} value={item.model.id}>
                {item.description.slice(0, 70) || algorithmLabel(item.model.algorithm)} · 제공 {formatRecordedAt(item.offered_at)}</option>)}
            </select>
            : <strong>{algorithmLabel(chosen.model.algorithm)}</strong>}
          {endpoint && <span className={onlineReady ? "chip chip-ready" : "chip"}>
            {onlineReady ? "온라인 API 사용 가능" : "온라인 API 준비 중"}</span>}
          <button type="button" className="ghost" onClick={() => onOpenResource("model", chosen.model.id)}>모델 상세 →</button>
          <p className="muted model-offering-description">{chosen.description || "아직 작성된 제공 설명이 없습니다."}</p>
          {!chosen.offered && <p className="hint">사용자에게 제공되지 않은 판본입니다. {isAdministrator
            ? "관리자 권한으로 적용합니다." : "소유자 권한으로 적용합니다."}</p>}
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

    {chosen && <ModelApiUsage model={chosen.model} endpoint={endpoint} endpointError={current?.error ?? ""}
      onRecheckEndpoint={() => setServingCheck(value => value + 1)} canInfer={canInfer}
      lastRun={lastRun?.modelRevisionId === chosen.model.id ? lastRun : undefined}
      datasetId={selectedDatasetId && readyDatasetId === selectedDatasetId ? selectedDatasetId : undefined} />}

    {topic && <details className="panel playground-details">
      <summary>입력 조건 · 모델 정보</summary>
      <p>모델은 사용자·호스트 단위로 집계한 숫자형 활동 Feature {topic.required_features.length}개를 이름과 자료형 그대로 요구합니다.
        컬럼 순서는 달라도 되며, 원시 로그에서 Feature를 자동으로 만들지 않습니다. 관측 시각 컬럼은 선택입니다.</p>
      {[...activityFeatureGroups.map(group => ({ id: group.id, title: group.title,
        names: topic.required_features.filter(name => (group.features as readonly string[]).includes(name)) })),
        { id: "other", title: "그 밖의 Feature", names: topic.required_features.filter(name =>
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
