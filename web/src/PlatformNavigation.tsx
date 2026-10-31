import { useCallback, useEffect, useRef, useState } from "react";
import { modelLabLabels } from "./ModelLabTaxonomy";
import { useTopicCatalog } from "./ModelLabGuide";

export type WorkspaceTab =
  | "overview" | "understanding" | "preview" | "profile" | "preparation"
  | "learning" | "task" | "topic" | "pipelines" | "ml" | "scoring" | "policies" | "evaluations" | "explanations"
  | "jobs" | "ml-jobs" | "serving" | "storage" | "audit";
export type MLResourceKind = "model" | "scoring" | "policy" | "evaluation" | "explanation";
export type ResourceRoute = { kind: MLResourceKind; id: string } | null;
export type MLNavigationContext = {
  datasetId?: string; modelId?: string; scoringId?: string;
  dataJobId?: string;
  dataJobKind?: "data" | "exploration";
  pipelineRunId?: string;
  /** 학습 유형·분석 과제 화면의 분류 ID. */
  taxonomyId?: string;
};
type WorkspaceRoute = {
  tab: WorkspaceTab; resource: ResourceRoute; context: MLNavigationContext;
};

const MODEL_INPUT_TABS: ReadonlyArray<WorkspaceTab> = ["ml", "topic", "scoring", "pipelines"];

const DATA_TABS: ReadonlyArray<WorkspaceTab> = ["understanding", "preparation", "preview", "profile", "jobs"];
const TAXONOMY_TABS: ReadonlyArray<WorkspaceTab> = ["learning", "task"];

function isDecisionTab(tab: WorkspaceTab): boolean {
  return tab === "policies" || tab === "evaluations" || tab === "explanations";
}

function inputDatasetContext(params: URLSearchParams): MLNavigationContext {
  return params.get("dataset") ? { datasetId: params.get("dataset")! } : {};
}

function pipelineContext(params: URLSearchParams): MLNavigationContext {
  return params.get("pipelineRun") ? { pipelineRunId: params.get("pipelineRun")! } : {};
}

function contextAt(tab: WorkspaceTab, search: string): MLNavigationContext {
  const params = new URLSearchParams(search);
  if (tab === "ml") return inputDatasetContext(params);
  if (tab === "topic") return { ...inputDatasetContext(params),
    ...(params.get("model") ? { modelId: params.get("model")! } : {}) };
  if (tab === "pipelines") return inputDatasetContext(params);
  if (DATA_TABS.includes(tab)) {
    return { datasetId: params.get("dataset") ?? "", ...pipelineContext(params),
      ...(tab === "jobs" && params.get("job") ? {
        dataJobId: params.get("job")!,
        dataJobKind: params.get("type") === "exploration" ? "exploration" as const : "data" as const,
      } : {}) };
  }
  if (tab === "serving") return pipelineContext(params);
  if (tab === "scoring") return { modelId: params.get("model") ?? "", ...inputDatasetContext(params), ...pipelineContext(params) };
  if (isDecisionTab(tab)) return { scoringId: params.get("scoring") ?? "", ...pipelineContext(params) };
  return {};
}

export function contextPath(tab: WorkspaceTab, context: MLNavigationContext): string {
  const params = new URLSearchParams();
  if (DATA_TABS.includes(tab) && context.datasetId) params.set("dataset", context.datasetId);
  if (tab === "jobs" && context.dataJobId) {
    params.set("job", context.dataJobId);
    if (context.dataJobKind === "exploration") params.set("type", "exploration");
  }
  if ((tab === "scoring" || tab === "topic") && context.modelId) params.set("model", context.modelId);
  if (isDecisionTab(tab) && context.scoringId) params.set("scoring", context.scoringId);
  if (context.datasetId && MODEL_INPUT_TABS.includes(tab)) params.set("dataset", context.datasetId);
  if ((DATA_TABS.includes(tab) || tab === "scoring" || isDecisionTab(tab) || tab === "serving") && context.pipelineRunId) params.set("pipelineRun", context.pipelineRunId);
  const base = tab === "pipelines" && context.pipelineRunId
    ? `${paths.pipelines}/runs/${encodeURIComponent(context.pipelineRunId)}`
    : TAXONOMY_TABS.includes(tab) && context.taxonomyId
      ? `${paths[tab]}/${encodeURIComponent(context.taxonomyId)}` : paths[tab];
  return base + (params.size ? "?" + params.toString() : "");
}

const paths: Record<WorkspaceTab, string> = {
  overview: "/", understanding: "/data", preview: "/data/query",
  profile: "/data/profile", preparation: "/data/prepare",
  learning: "/learning-types", task: "/tasks",
  topic: "/topics/activity-anomaly", pipelines: "/pipelines", ml: "/models",
  scoring: "/scoring", policies: "/policies",
  evaluations: "/evaluations", explanations: "/explanations",
  jobs: "/activity/jobs", "ml-jobs": "/activity/ml", serving: "/operations/serving", storage: "/operations/storage", audit: "/operations/audit",
};
const resourcePaths: Record<MLResourceKind, { base: string; tab: WorkspaceTab }> = {
  model: { base: "/models", tab: "ml" },
  scoring: { base: "/scoring/results", tab: "scoring" },
  policy: { base: "/policies", tab: "policies" },
  evaluation: { base: "/evaluations", tab: "evaluations" },
  explanation: { base: "/explanations", tab: "explanations" },
};
export function resourcePath(kind: MLResourceKind, id: string, context: MLNavigationContext = {}): string {
  const path = `${resourcePaths[kind].base}/${encodeURIComponent(id)}`;
  const params = new URLSearchParams();
  if (context.datasetId && kind === "model") params.set("dataset", context.datasetId);
  if (context.pipelineRunId) params.set("pipelineRun", context.pipelineRunId);
  return path + (params.size ? "?" + params.toString() : "");
}

export function routeAt(pathname: string, search: string): WorkspaceRoute {
  for (const tab of TAXONOMY_TABS) {
    if (pathname.startsWith(paths[tab] + "/")) {
      const id = pathname.slice(paths[tab].length + 1);
      if (id && !id.includes("/")) {
        try { return { tab, resource: null, context: { taxonomyId: decodeURIComponent(id) } }; }
        catch { /* 잘못 인코딩된 URL은 기본 화면으로 돌린다. */ }
      }
    }
  }
  if (pathname.startsWith("/pipelines/runs/")) {
    const id = pathname.slice("/pipelines/runs/".length);
    if (id && !id.includes("/")) {
      try { return { tab: "pipelines", resource: null,
        context: { ...inputDatasetContext(new URLSearchParams(search)), pipelineRunId: decodeURIComponent(id) } }; }
      catch { /* 잘못 인코딩된 URL은 기본 화면으로 돌린다. */ }
    }
  }
  for (const [kind, config] of Object.entries(resourcePaths) as
      Array<[MLResourceKind, { base: string; tab: WorkspaceTab }]>) {
    if (pathname.startsWith(config.base + "/")) {
      const id = pathname.slice(config.base.length + 1);
      if (id && !id.includes("/")) {
        try { return { tab: config.tab, resource: { kind, id: decodeURIComponent(id) },
          context: { ...(kind === "model" ? inputDatasetContext(new URLSearchParams(search)) : {}),
            ...pipelineContext(new URLSearchParams(search)) } }; }
        catch { break; }
      }
    }
  }
  const tab = (Object.keys(paths) as WorkspaceTab[]).find(key => paths[key] === pathname);
  return { tab: tab ?? "overview", resource: null,
    context: contextAt(tab ?? "overview", search) };
}

/** 독립 화면 간에 목적과 관련된 선택만 전달한다. */
export function nextNavigationContext(previous: WorkspaceRoute, tab: WorkspaceTab): MLNavigationContext {
  if (tab === "pipelines") return {
    ...(previous.context.datasetId ? { datasetId: previous.context.datasetId } : {}),
    ...(previous.context.pipelineRunId ? { pipelineRunId: previous.context.pipelineRunId } : {}),
  };
  // 결과 상세에서 모델 영역 탭을 직접 선택해도 이미 확인한 리소스를 전달한다.
  if (previous.resource?.kind === "scoring" && isDecisionTab(tab)) {
    return { scoringId: previous.resource.id,
      ...(previous.context.pipelineRunId ? { pipelineRunId: previous.context.pipelineRunId } : {}) };
  }
  if (previous.resource?.kind === "model" && tab === "scoring") {
    return { modelId: previous.resource.id, ...(previous.context.datasetId ? { datasetId: previous.context.datasetId } : {}),
      ...(previous.context.pipelineRunId ? { pipelineRunId: previous.context.pipelineRunId } : {}) };
  }
  if (DATA_TABS.includes(tab) && DATA_TABS.includes(previous.tab)) {
    return { ...(previous.context.datasetId ? { datasetId: previous.context.datasetId } : {}),
      ...(previous.context.pipelineRunId ? { pipelineRunId: previous.context.pipelineRunId } : {}) };
  }
  if (previous.context.datasetId &&
      ((MODEL_INPUT_TABS.includes(tab) && (DATA_TABS.includes(previous.tab) || MODEL_INPUT_TABS.includes(previous.tab))) ||
       (DATA_TABS.includes(tab) && MODEL_INPUT_TABS.includes(previous.tab)))) {
    return { datasetId: previous.context.datasetId };
  }
  if (isDecisionTab(tab) && isDecisionTab(previous.tab)) {
    return {
      scoringId: previous.context.scoringId,
      ...(previous.context.pipelineRunId ? { pipelineRunId: previous.context.pipelineRunId } : {}),
    };
  }
  return {};
}

/** 탭과 불변 결과 주소를 같은 탐색 상태로 유지한다. */
export function useWorkspaceNavigation() {
  const [route, setRoute] = useState(
    () => routeAt(window.location.pathname, window.location.search));
  const current = useRef(route);
  useEffect(() => {
    const sync = () => {
      current.current = routeAt(window.location.pathname, window.location.search);
      setRoute(current.current);
      requestAnimationFrame(() => document.getElementById("sos-page-heading")?.focus());
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const update = useCallback((next: WorkspaceRoute, path: string) => {
    if (window.location.pathname + window.location.search !== path) {
      window.history.pushState(null, "", path);
    }
    current.current = next;
    setRoute(next);
    requestAnimationFrame(() => document.getElementById("sos-page-heading")?.focus());
  }, []);
  const navigateWithContext = useCallback((tab: WorkspaceTab, context: MLNavigationContext) => {
    const path = contextPath(tab, context);
    const url = new URL(path, window.location.origin);
    const next = routeAt(url.pathname, url.search).context;
    update({ tab, resource: null, context: next }, contextPath(tab, next));
  }, [update]);
  const navigate = useCallback((target: WorkspaceTab | ((previous: WorkspaceTab) => WorkspaceTab)) => {
    const previous = current.current;
    const tab = typeof target === "function" ? target(previous.tab) : target;
    const context = nextNavigationContext(previous, tab);
    update({ tab, resource: null, context }, contextPath(tab, context));
  }, [update]);
  const selectContext = useCallback((context: MLNavigationContext) => {
    const previous = current.current;
    if (previous.resource) return;
    const path = contextPath(previous.tab, context);
    const parsed = { ...contextAt(previous.tab, path.split("?")[1] ?? ""),
      ...(previous.tab === "pipelines" && context.pipelineRunId ? { pipelineRunId: context.pipelineRunId } : {}) };
    // 사용자가 확인한 Dataset/Model/Scoring 선택은 뒤로 가기로 복원할 수 있어야 한다.
    // 입력 한 글자마다 호출하지 않으며, 같은 화면의 선택은 헤더 포커스를 움직이지 않는다.
    if (window.location.pathname + window.location.search !== path) {
      window.history.pushState(null, "", path);
    }
    const next = { ...previous, context: parsed };
    current.current = next;
    setRoute(next);
  }, [update]);
  const openResource = useCallback((kind: MLResourceKind, id: string) => {
    if (!id) return;
    const previous = current.current;
    const resultContext: MLNavigationContext = kind === "model" && previous.context.datasetId
      ? { datasetId: previous.context.datasetId } : {};
    const context = { ...resultContext,
      ...(previous.context.pipelineRunId ? { pipelineRunId: previous.context.pipelineRunId } : {}) };
    update({ tab: resourcePaths[kind].tab, resource: { kind, id }, context },
      resourcePath(kind, id, context));
  }, [update]);
  return { tab: route.tab, resource: route.resource, context: route.context,
    navigate, navigateWithContext, selectContext, openResource };
}

type NavItem = { tab: WorkspaceTab; label: string };
// 목적별 묶음. ML 분석은 학습 유형 → 분석 과제 → 주제 트리를 먼저 보이고 모델 목록을 잇는다.
const groups: ReadonlyArray<{ title: string; items: NavItem[]; taxonomy?: boolean }> = [
  { title: "", items: [{ tab: "overview", label: "홈" }] },
  { title: "데이터", items: [
    { tab: "understanding", label: "데이터 탐색" },
    { tab: "preparation", label: "데이터 준비" },
  ] },
  { title: "ML 분석", taxonomy: true, items: [{ tab: "ml", label: "모델 활용" }] },
  { title: "작업·운영", items: [
    { tab: "jobs", label: "작업 이력" },
    // 등록 파이프라인은 학습 권한 계정이 모델을 공급하는 운영 도구다.
    { tab: "pipelines", label: "등록 파이프라인" },
    { tab: "serving", label: "온라인 서빙" },
    { tab: "storage", label: "저장소 상태" },
    { tab: "audit", label: "보안 감사" },
  ] },
];

const modelTools: ReadonlyArray<NavItem> = [
  { tab: "scoring", label: "모델 적용" },
  { tab: "evaluations", label: "평가" },
  { tab: "policies", label: "판정 정책" },
  { tab: "explanations", label: "점수 설명" },
];
export function isModelsArea(tab: WorkspaceTab): boolean {
  return ["ml", ...modelTools.map(item => item.tab)].includes(tab);
}

/** 모델 적용 이후의 결과 도구는 모델 활용 문맥 안에서 제공한다. */
export function ModelAreaNavigation({ active, context, resource, onNavigate }: {
  active: WorkspaceTab;
  context: MLNavigationContext;
  resource: ResourceRoute;
  onNavigate: (tab: WorkspaceTab) => void;
}) {
  const link = (item: NavItem, current: boolean) => <a key={item.tab}
    href={contextPath(item.tab, nextNavigationContext({ tab: active, resource, context }, item.tab))}
    aria-current={current ? "page" : undefined} className="model-area-link"
    onClick={event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); onNavigate(item.tab);
    }}>{item.label}</a>;
  // 모델 목록·상세에서는 아직 다룰 결과가 없으므로 결과 도구를 보이지 않는다.
  if (active === "ml") return null;
  return <nav className="model-area-navigation model-tools" aria-label="모델 적용과 결과 도구">
    {modelTools.map(item => link(item, active === item.tab))}
  </nav>;
}

/** 영역별 진입점은 내부 결과가 없어도 언제나 탐색 가능하다. */
export function PlatformNavigation({ active, onNavigate, onOpenTaxonomy, canInspectStorage, canViewAudit, canViewDataset = true,
  canPrepareData = true, canOperatePipelines = false, canManageServing = false, context = {}, resource = null }: {
  active: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenTaxonomy: (tab: "learning" | "task", id: string) => void;
  canInspectStorage: boolean;
  canViewAudit: boolean;
  canViewDataset?: boolean;
  canPrepareData?: boolean;
  canOperatePipelines?: boolean;
  canManageServing?: boolean;
  context?: MLNavigationContext;
  resource?: ResourceRoute;
}) {
  // 주제 트리는 데이터 조회 권한이 있는 계정의 ML 분석 메뉴에만 있다. 운영 전용 계정은 조회하지 않는다.
  const catalog = useTopicCatalog(canViewDataset);
  const catalogError = Boolean(catalog.error);
  const catalogLoading = catalog.items === null && !catalogError;
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  // 화면에서 모델을 적용할 수 있는 주제만 연결한다. 미구현 주제 링크를 생성하지 않는다.
  const topics = catalog.tree;
  const taxonomyLink = (tab: "learning" | "task", id: string, label: string, kind: string) => <a
    href={contextPath(tab, { taxonomyId: id })} className="workspace-link model-lab-tree-link"
    aria-current={active === tab && context.taxonomyId === id ? "page" : undefined}
    onClick={event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      onOpenTaxonomy(tab, id);
    }}>
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M2.5 5.5h6l2 2h7v8h-15z"/>
    </svg>
    <span className="model-lab-tree-name">{label}</span>
    <small>{kind}</small>
  </a>;
  const isExpanded = (key: string) => !collapsed[key];
  const toggle = (key: string) => setCollapsed(previous => ({ ...previous, [key]: !previous[key] }));

  const hrefFor = (tab: WorkspaceTab) => contextPath(tab,
    nextNavigationContext({ tab: active, resource, context }, tab));
  const isCurrent = (tab: WorkspaceTab) =>
    tab === "understanding" ? ["understanding", "preview", "profile"].includes(active)
      : tab === "ml" ? isModelsArea(active)
      : tab === "jobs" ? ["jobs", "ml-jobs"].includes(active) : active === tab;
  // 실행할 수 없는 기능의 메뉴는 표시하지 않는다. 서버 권한 검사는 별도로 유지된다.
  const visible = (item: NavItem) =>
    item.tab === "pipelines" ? canOperatePipelines
      : item.tab === "serving" ? canManageServing
      : item.tab === "storage" ? canInspectStorage
      : item.tab === "audit" ? canViewAudit
      : item.tab === "preparation" ? canViewDataset && canPrepareData
      : canViewDataset;
  return <nav className="global-nav platform-nav" aria-label="플랫폼 메뉴">
    {groups.filter(group => group.items.some(visible))
      .map(group => <div className="platform-nav-group" key={group.title || "home"}>
      {group.title && <span className="section-title">{group.title}</span>}
      {group.taxonomy && <div className="model-lab-tree" aria-label="등록된 분석 주제">
        {catalogLoading && <p className="model-lab-tree-feedback" role="status">주제 목록을 불러오는 중입니다.</p>}
        {!catalogLoading && !catalogError && topics.length === 0 &&
          <p className="model-lab-tree-feedback">현재 사용 가능한 주제가 없습니다.</p>}
        {catalogError && <div className="model-lab-tree-feedback" role="status">
          <span>분석 주제 목록을 불러오지 못했습니다.</span>
          <button type="button" className="ghost" onClick={catalog.reload}>다시 조회</button>
        </div>}
        {(catalogLoading || catalogError) && <a
          href={hrefFor("topic")} className="workspace-link model-lab-topic-link"
          aria-current={active === "topic" ? "page" : undefined}
          onClick={event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            onNavigate("topic");
          }}>
          <span className="model-lab-tree-name">활동 이상 탐지 화면 열기</span>
        </a>}
        {topics.map(learning => {
          const learningKey = "learning:" + learning.id;
          return <div className="model-lab-tree-group" key={learning.id}>
            <div className="model-lab-tree-row">
              <button type="button" className="ghost model-lab-tree-caret" aria-expanded={isExpanded(learningKey)}
                aria-label={`${learning.label} ${isExpanded(learningKey) ? "접기" : "펼치기"}`}
                onClick={() => toggle(learningKey)}>{isExpanded(learningKey) ? "▾" : "▸"}</button>
              {taxonomyLink("learning", learning.id, learning.label, "학습 유형")}
            </div>
            {isExpanded(learningKey) && <div className="model-lab-tree-children">
              {learning.tasks.map(task => {
                const taskKey = learningKey + ":task:" + task.id;
                return <div key={task.id}>
                  <div className="model-lab-tree-row">
                    <button type="button" className="ghost model-lab-tree-caret" aria-expanded={isExpanded(taskKey)}
                      aria-label={`${task.label} ${isExpanded(taskKey) ? "접기" : "펼치기"}`}
                      onClick={() => toggle(taskKey)}>{isExpanded(taskKey) ? "▾" : "▸"}</button>
                    {taxonomyLink("task", task.id, task.label, "분석 과제")}
                  </div>
                  {isExpanded(taskKey) && <div className="model-lab-tree-children">
                    {task.topics.map(topic => <a key={topic.id}
                      className="workspace-link model-lab-topic-link" href={hrefFor("topic")}
                      aria-current={active === "topic" ? "page" : undefined}
                      onClick={event => {
                        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                        event.preventDefault();
                        onNavigate("topic");
                      }}>
                      <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                        <rect x="4" y="2.5" width="12" height="15" rx="1.5"/>
                        <path d="M7 12l2-3 2 5 2-3"/>
                      </svg>
                      <span className="model-lab-tree-name">{modelLabLabels(topic)[2]}</span>
                      <small>주제</small>
                    </a>)}
                  </div>}
                </div>;
              })}
            </div>}
          </div>;
        })}
      </div>}
      {group.items.filter(visible).map(item => <a key={item.tab} href={hrefFor(item.tab)}
        className="workspace-link" aria-current={isCurrent(item.tab) ? "page" : undefined}
        onClick={event => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onNavigate(item.tab);
        }}>
        <span>{item.label}</span>
      </a>)}
    </div>)}
  </nav>;
}
