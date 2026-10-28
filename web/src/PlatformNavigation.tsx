import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "./api";
import { modelLabLabels, modelLabTree } from "./ModelLabTaxonomy";
import type { PipelineDefinition } from "./types";

export type WorkspaceTab =
  | "overview" | "understanding" | "preview" | "profile" | "preparation"
  | "pipelines" | "ml" | "research" | "training" | "scoring" | "policies"
  | "evaluations" | "explanations"
  | "jobs" | "ml-jobs" | "pipeline-jobs" | "storage" | "audit";
export type MLResourceKind = "model" | "scoring" | "policy" | "evaluation" | "explanation";
export type ResourceRoute = { kind: MLResourceKind; id: string } | null;
export type MLNavigationContext = {
  datasetId?: string; modelId?: string; scoringId?: string;
  pipelineRunId?: string; pipelineReturn?: boolean; candidateRowIndex?: number;
  pipelineView?: "learn" | "use";
  modelGroupKey?: string;
  modelLabReturn?: boolean;
  dataJobId?: string;
  dataJobKind?: "data" | "exploration";
  pipelineAnalysisSection?: "overview" | "training" | "calibration" | "test";
};
type WorkspaceRoute = {
  tab: WorkspaceTab; resource: ResourceRoute; context: MLNavigationContext;
};

function isDecisionTab(tab: WorkspaceTab): boolean {
  return tab === "policies" || tab === "evaluations" || tab === "explanations";
}

function candidateRowAt(params: URLSearchParams): number | undefined {
  const value = params.get("candidate");
  if (value === null || !/^(0|[1-9][0-9]*)$/.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : undefined;
}

function analysisSectionAt(params: URLSearchParams): MLNavigationContext["pipelineAnalysisSection"] {
  const value = params.get("section");
  return value === "overview" || value === "training" ||
    value === "calibration" || value === "test" ? value : undefined;
}

function modelGroupAt(params: URLSearchParams): string | undefined {
  const group = params.get("group");
  return group && /^[a-z][a-z0-9-]{0,39}$/.test(group) ? group : undefined;
}

function contextAt(tab: WorkspaceTab, search: string): MLNavigationContext {
  const params = new URLSearchParams(search);
  if (tab === "pipelines") {
    const runId = params.get("run") ?? "";
    return {
      datasetId: params.get("dataset") ?? "",
      pipelineRunId: runId,
      ...(!runId && (params.get("view") === "learn" || params.get("view") === "use")
        ? { pipelineView: params.get("view") as "learn" | "use" } : {}),
      ...(!runId && params.get("view") === "use" && params.get("model")
        ? { modelId: params.get("model")! } : {}),
      ...(!runId && params.get("view") === "use" && modelGroupAt(params)
        ? { modelGroupKey: modelGroupAt(params) } : {}),
      ...(runId && candidateRowAt(params) !== undefined
        ? { candidateRowIndex: candidateRowAt(params) } : {}),
      ...(runId && analysisSectionAt(params) !== undefined
        ? { pipelineAnalysisSection: analysisSectionAt(params) } : {}),
    };
  }
  if (tab === "understanding" || tab === "preparation" || tab === "preview" ||
      tab === "profile" || tab === "jobs") {
    const pipelineReturn = params.get("from") === "pipeline";
    return { pipelineReturn, datasetId: params.get("dataset") ?? "",
      ...(tab === "jobs" && params.get("job") ? {
        dataJobId: params.get("job")!,
        dataJobKind: params.get("type") === "exploration" ? "exploration" as const : "data" as const,
      } : {}),
      ...(pipelineReturn && params.get("run") ? {
        pipelineRunId: params.get("run")!,
        ...(candidateRowAt(params) !== undefined ? { candidateRowIndex: candidateRowAt(params) } : {}),
        ...(analysisSectionAt(params) ? { pipelineAnalysisSection: analysisSectionAt(params) } : {}),
      } : {}) };
  }
  if (tab === "training" || tab === "ml" || tab === "research") return { datasetId: params.get("dataset") ?? "",
    ...(modelGroupAt(params) ? { modelGroupKey: modelGroupAt(params) } : {}) };
  if (tab === "scoring") return { ...resourceContext(search), modelId: params.get("model") ?? "",
    ...(modelGroupAt(params) ? { modelGroupKey: modelGroupAt(params) } : {}) };
  if (isDecisionTab(tab)) return {
    scoringId: params.get("scoring") ?? "",
    ...(params.get("from") === "model-lab" && params.get("model")
      ? { modelLabReturn: true, modelId: params.get("model")!,
          ...(modelGroupAt(params) ? { modelGroupKey: modelGroupAt(params) } : {}) } : {}),
  };
  return {};
}

/** 동일한 Run의 Dataset 참조를 정정할 때 조사 문맥을 보존한다. 다른 Run은 새 선택이다. */
export function pipelineRunSelectionContext(
  previous: MLNavigationContext, runId: string, datasetId: string,
): MLNavigationContext {
  return {
    datasetId, pipelineRunId: runId,
    ...(!runId ? { pipelineView: "learn" as const } : {}),
    ...(runId && runId === previous.pipelineRunId
      ? { candidateRowIndex: previous.candidateRowIndex,
          pipelineAnalysisSection: previous.pipelineAnalysisSection }
      : {}),
  };
}

export function contextPath(tab: WorkspaceTab, context: MLNavigationContext): string {
  const params = new URLSearchParams();
  if (tab === "pipelines") {
    if (context.datasetId) params.set("dataset", context.datasetId);
    if (!context.pipelineRunId && context.pipelineView) params.set("view", context.pipelineView);
    if (!context.pipelineRunId && context.pipelineView === "use" && context.modelId)
      params.set("model", context.modelId);
    if (!context.pipelineRunId && context.pipelineView === "use" &&
        context.modelGroupKey && context.modelGroupKey !== "default")
      params.set("group", context.modelGroupKey);
    if (context.pipelineRunId) params.set("run", context.pipelineRunId);
    if (context.pipelineRunId && context.candidateRowIndex !== undefined)
      params.set("candidate", String(context.candidateRowIndex));
    if (context.pipelineRunId && context.pipelineAnalysisSection)
      params.set("section", context.pipelineAnalysisSection);
  }
  if ((tab === "understanding" || tab === "preparation" || tab === "preview" ||
        tab === "profile" || tab === "jobs" || tab === "scoring") && context.pipelineReturn) {
    params.set("from", "pipeline");
    if (context.datasetId) params.set("dataset", context.datasetId);
    if (context.pipelineRunId) {
      params.set("run", context.pipelineRunId);
      if (context.candidateRowIndex !== undefined) params.set("candidate", String(context.candidateRowIndex));
      if (context.pipelineAnalysisSection) params.set("section", context.pipelineAnalysisSection);
    }
  }
  if (["understanding", "preparation", "preview", "profile", "jobs"].includes(tab) && context.datasetId)
    params.set("dataset", context.datasetId);
  if (tab === "jobs" && context.dataJobId) {
    params.set("job", context.dataJobId);
    if (context.dataJobKind === "exploration") params.set("type", "exploration");
  }
  if ((tab === "training" || tab === "ml" || tab === "research") && context.datasetId) {
    params.set("dataset", context.datasetId);
  }
  if (tab === "scoring" && context.modelId) params.set("model", context.modelId);
  if ((tab === "ml" || tab === "research" || tab === "training" || tab === "scoring") &&
      context.modelGroupKey && context.modelGroupKey !== "default")
    params.set("group", context.modelGroupKey);
  if (isDecisionTab(tab) && context.scoringId) params.set("scoring", context.scoringId);
  if ((isDecisionTab(tab) || tab === "scoring") && context.modelLabReturn && context.modelId) {
    params.set("from", "model-lab");
    params.set("model", context.modelId);
    if (context.modelGroupKey && context.modelGroupKey !== "default")
      params.set("group", context.modelGroupKey);
  }
  return paths[tab] + (params.size ? "?" + params.toString() : "");
}

const paths: Record<WorkspaceTab, string> = {
  overview: "/", understanding: "/data", preview: "/data/query",
  profile: "/data/profile", preparation: "/data/prepare",
  pipelines: "/pipelines", ml: "/models", research: "/research", training: "/training",
  scoring: "/scoring", policies: "/policies",
  evaluations: "/evaluations", explanations: "/explanations",
  jobs: "/activity/jobs", "ml-jobs": "/activity/ml", "pipeline-jobs": "/activity/pipelines", storage: "/operations/storage", audit: "/operations/audit",
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
  if (context.modelLabReturn && context.modelId && !context.pipelineRunId) {
    return path + "?" + new URLSearchParams({ from: "model-lab", model: context.modelId,
      ...(context.modelGroupKey && context.modelGroupKey !== "default"
        ? { group: context.modelGroupKey } : {}) });
  }
  if (!context.pipelineReturn || !context.pipelineRunId) return path +
    (context.modelGroupKey && context.modelGroupKey !== "default"
      ? "?" + new URLSearchParams({ group: context.modelGroupKey }) : "");
  const params = new URLSearchParams({ from: "pipeline", run: context.pipelineRunId });
  if (context.datasetId) params.set("dataset", context.datasetId);
  if (context.candidateRowIndex !== undefined)
    params.set("candidate", String(context.candidateRowIndex));
  if (context.pipelineAnalysisSection)
    params.set("section", context.pipelineAnalysisSection);
  return path + "?" + params.toString();
}

function resourceContext(search: string): MLNavigationContext {
  const params = new URLSearchParams(search);
  if (params.get("from") === "model-lab" && params.get("model"))
    return { modelLabReturn: true, modelId: params.get("model")!,
      ...(modelGroupAt(params) ? { modelGroupKey: modelGroupAt(params) } : {}) };
  if (params.get("from") !== "pipeline" || !params.get("run")) return modelGroupAt(params)
    ? { modelGroupKey: modelGroupAt(params) } : {};
  return { pipelineReturn: true, pipelineRunId: params.get("run")!,
    datasetId: params.get("dataset") ?? "",
    ...(candidateRowAt(params) !== undefined ? { candidateRowIndex: candidateRowAt(params) } : {}),
    ...(analysisSectionAt(params) !== undefined
      ? { pipelineAnalysisSection: analysisSectionAt(params) } : {}) };
}
export function routeAt(pathname: string, search: string): WorkspaceRoute {
  for (const [kind, config] of Object.entries(resourcePaths) as
      Array<[MLResourceKind, { base: string; tab: WorkspaceTab }]>) {
    if (pathname.startsWith(config.base + "/")) {
      const id = pathname.slice(config.base.length + 1);
      if (id && !id.includes("/")) {
        try { return { tab: config.tab, resource: { kind, id: decodeURIComponent(id) },
          context: resourceContext(search) }; }
        catch { break; }
      }
    }
  }
  // 과거 모델 경험 주소도 실제 모델 활용 진입점으로 연결한다.
  if (pathname === "/model-experience") {
    return { tab: "ml", resource: null, context: contextAt("ml", search) };
  }
  const tab = (Object.keys(paths) as WorkspaceTab[]).find(key => paths[key] === pathname);
  return { tab: tab ?? "overview", resource: null,
    context: contextAt(tab ?? "overview", search) };
}

/** 독립 화면 간에 목적과 관련된 선택만 전달한다. */
export function nextNavigationContext(previous: WorkspaceRoute, tab: WorkspaceTab): MLNavigationContext {
  // 결과 상세에서 모델 영역 탭을 직접 선택해도 이미 확인한 리소스를 전달한다.
  if (previous.resource?.kind === "scoring" && isDecisionTab(tab)) {
    return { scoringId: previous.resource.id };
  }
  if (previous.resource?.kind === "model" && tab === "scoring") {
    return { modelId: previous.resource.id,
      ...(previous.context.modelGroupKey ? { modelGroupKey: previous.context.modelGroupKey } : {}) };
  }
  if (["understanding", "preparation", "preview", "profile", "jobs"].includes(tab) &&
      ["understanding", "preparation", "preview", "profile", "jobs"].includes(previous.tab)) {
    return { pipelineReturn: previous.context.pipelineReturn,
      ...(previous.context.datasetId
        ? { datasetId: previous.context.datasetId } : {}),
      ...(previous.context.pipelineReturn && previous.context.pipelineRunId ? {
        pipelineRunId: previous.context.pipelineRunId,
        ...(previous.context.candidateRowIndex !== undefined
          ? { candidateRowIndex: previous.context.candidateRowIndex } : {}),
        ...(previous.context.pipelineAnalysisSection
          ? { pipelineAnalysisSection: previous.context.pipelineAnalysisSection } : {}),
      } : {}) };
  }
  if (isDecisionTab(tab) && isDecisionTab(previous.tab)) {
    return {
      scoringId: previous.context.scoringId,
      ...(previous.context.modelLabReturn && previous.context.modelId
        ? { modelLabReturn: true, modelId: previous.context.modelId,
            modelGroupKey: previous.context.modelGroupKey } : {}),
    };
  }
  if (tab === "research" && previous.tab === "pipelines" && previous.context.pipelineView === "use") {
    return { datasetId: previous.context.datasetId, modelGroupKey: previous.context.modelGroupKey };
  }
  if ((tab === "training" || tab === "ml" || tab === "research" || tab === "scoring") &&
      (previous.tab === "training" || previous.tab === "ml" || previous.tab === "research" || previous.tab === "scoring")) {
    return { ...(previous.context.datasetId ? { datasetId: previous.context.datasetId } : {}),
      ...(previous.context.modelGroupKey ? { modelGroupKey: previous.context.modelGroupKey } : {}) };
  }
  return {};
}

/** 탭과 불변 결과 주소를 같은 탐색 상태로 유지한다. */
export function useWorkspaceNavigation() {
  const [route, setRoute] = useState(
    () => routeAt(window.location.pathname, window.location.search));
  const current = useRef(route);
  useEffect(() => {
    if (window.location.pathname === "/model-experience") {
      window.history.replaceState(null, "", contextPath("ml", current.current.context));
    }
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
    const next = contextAt(tab, contextPath(tab, context).split("?")[1] ?? "");
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
    const parsed = contextAt(previous.tab, path.split("?")[1] ?? "");
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
    // Pipeline에서 열었던 결과 판본은 직접 링크·새로고침에서도 원래 Run에 돌아갈 수 있다.
    const fromPipeline = (previous.tab === "pipelines" || previous.context.pipelineReturn === true || previous.resource !== null)
      && previous.context.pipelineReturn !== false;
    const fromLabUse = (previous.tab === "pipelines" && previous.context.pipelineView === "use") ||
      previous.context.modelLabReturn === true;
    const context: MLNavigationContext = fromPipeline && previous.context.pipelineRunId
      ? { pipelineReturn: true, pipelineRunId: previous.context.pipelineRunId,
          datasetId: previous.context.datasetId,
          candidateRowIndex: previous.context.candidateRowIndex,
          pipelineAnalysisSection: previous.context.pipelineAnalysisSection }
      : fromLabUse && previous.context.modelId
        ? { modelLabReturn: true, modelId: previous.context.modelId,
            modelGroupKey: previous.context.modelGroupKey }
        : previous.context.modelGroupKey ? { modelGroupKey: previous.context.modelGroupKey } : {};
    update({ tab: resourcePaths[kind].tab, resource: { kind, id }, context },
      resourcePath(kind, id, context));
  }, [update]);
  return { tab: route.tab, resource: route.resource, context: route.context,
    navigate, navigateWithContext, selectContext, openResource };
}

type NavItem = { tab: WorkspaceTab; label: string };
const groups: ReadonlyArray<{ title: string; items: NavItem[] }> = [
  { title: "WORKSPACE", items: [
    { tab: "overview", label: "홈" },
    { tab: "understanding", label: "데이터 탐색" },
    { tab: "ml", label: "모델 활용" },
    { tab: "research", label: "연구·실험" },
  ] },
  { title: "분석 주제", items: [{ tab: "pipelines", label: "활동 이상 탐지" }] },
  { title: "DATA TOOLS", items: [{ tab: "preparation", label: "데이터 준비" }] },
  { title: "ACTIVITY & OPERATIONS", items: [
    { tab: "jobs", label: "작업 이력" },
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
  return ["ml", "research", "training", ...modelTools.map(item => item.tab)].includes(tab);
}

/** 사용자 목적을 먼저 구분하고 독립 실행 도구는 활용 문맥 안에서 제공한다. */
export function ModelAreaNavigation({ active, context, resource, onNavigate }: {
  active: WorkspaceTab;
  context: MLNavigationContext;
  resource: ResourceRoute;
  onNavigate: (tab: WorkspaceTab) => void;
}) {
  const inResearch = active === "research" || active === "training";
  const link = (item: NavItem, current: boolean) => <a key={item.tab}
    href={contextPath(item.tab, nextNavigationContext({ tab: active, resource, context }, item.tab))}
    aria-current={current ? "page" : undefined} className="model-area-link"
    onClick={event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); onNavigate(item.tab);
    }}>{item.label}</a>;
  return <div className="model-area-navigation">
    <nav className="model-area-nav" aria-label="모델 작업 목적">
      {link({ tab: "ml", label: "모델 활용" }, !inResearch)}
      {link({ tab: "research", label: "연구·실험" }, inResearch)}
    </nav>
    {active === "training" && <p className="hint">연구·실험 / 새 학습</p>}
    {!inResearch && <details className="model-tools" open={active !== "ml"}>
      <summary>모델 적용·결과 도구</summary>
      <nav aria-label="모델 적용과 결과 도구">
        {modelTools.map(item => link(item, active === item.tab))}
      </nav>
    </details>}
  </div>;
}

/** 영역별 진입점은 내부 결과가 없어도 언제나 탐색 가능하다. */
export function PlatformNavigation({ active, onNavigate, canInspectStorage, canViewAudit, canViewDataset = true,
  context = {}, resource = null }: {
  active: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
  canInspectStorage: boolean;
  canViewAudit: boolean;
  canViewDataset?: boolean;
  context?: MLNavigationContext;
  resource?: ResourceRoute;
}) {
  const [catalog, setCatalog] = useState<PipelineDefinition[]>([]);
  const [catalogError, setCatalogError] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogReload, setCatalogReload] = useState(0);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!canViewDataset) { setCatalogLoading(false); return; }
    let active = true;
    setCatalogError(false); setCatalogLoading(true);
    void request<PipelineDefinition[]>("/api/pipelines")
      .then(items => { if (active) setCatalog(items); })
      .catch(() => { if (active) { setCatalog([]); setCatalogError(true); } })
      .finally(() => { if (active) setCatalogLoading(false); });
    return () => { active = false; };
  }, [catalogReload, canViewDataset]);
  // 현재 웹에서 실행할 수 있는 등록 Pipeline만 연결한다. 미구현 주제 링크를 생성하지 않는다.
  const topics = modelLabTree(catalog.filter(item => item.id === "activity-anomaly-if"));
  const isExpanded = (key: string) => !collapsed[key];
  const toggle = (key: string) => setCollapsed(previous => ({ ...previous, [key]: !previous[key] }));

  const hrefFor = (tab: WorkspaceTab) => contextPath(tab,
    nextNavigationContext({ tab: active, resource, context }, tab));
  const isCurrent = (tab: WorkspaceTab) =>
    tab === "understanding" ? ["understanding", "preview", "profile"].includes(active)
      : tab === "ml" ? isModelsArea(active) && active !== "research" && active !== "training"
      : tab === "research" ? active === "research" || active === "training"
      : tab === "jobs" ? ["jobs", "ml-jobs", "pipeline-jobs"].includes(active) : active === tab;
  return <nav className="global-nav platform-nav" aria-label="플랫폼 메뉴">
    {groups.filter(group => canViewDataset || group.items.some(item =>
      (item.tab === "storage" && canInspectStorage) || (item.tab === "audit" && canViewAudit)))
      .map(group => <div className="platform-nav-group" key={group.title}>
      <span className={group.title === "분석 주제" ? "section-title model-lab-tree-title" : "section-title"}>
        {group.title === "분석 주제" && <svg viewBox="0 0 20 20" width="15" height="15"
          fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M7 2h6M9 2v6l-5 7a2 2 0 0 0 1.7 3h8.6a2 2 0 0 0 1.7-3l-5-7V2M7 13h6"/>
        </svg>}
        {group.title}
      </span>
      {group.title === "분석 주제" ? <div className="model-lab-tree" aria-label="등록된 분석 주제">
        {catalogLoading && <p className="model-lab-tree-feedback" role="status">주제 목록을 불러오는 중입니다.</p>}
        {!catalogLoading && !catalogError && topics.length === 0 &&
          <p className="model-lab-tree-feedback">현재 사용 가능한 주제가 없습니다.</p>}
        {catalogError && <div className="model-lab-tree-feedback" role="status">
          <span>분석 주제 목록을 불러오지 못했습니다.</span>
          <button type="button" className="ghost" onClick={() => setCatalogReload(n => n + 1)}>다시 조회</button>
        </div>}
        {(catalogLoading || catalogError) && <a
          href={hrefFor("pipelines")} className="workspace-link model-lab-topic-link"
          aria-current={active === "pipelines" ? "page" : undefined}
          onClick={event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            onNavigate("pipelines");
          }}>
          <span className="model-lab-tree-name">활동 이상 탐지 화면 열기</span>
        </a>}
        {topics.map(learning => {
          const learningKey = "learning:" + learning.id;
          return <div className="model-lab-tree-group" key={learning.id}>
            <button type="button" className="ghost model-lab-tree-toggle"
              aria-expanded={isExpanded(learningKey)} onClick={() => toggle(learningKey)}>
              <span className="model-lab-tree-caret" aria-hidden="true">{isExpanded(learningKey) ? "▾" : "▸"}</span>
              <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <path d="M2.5 5.5h6l2 2h7v8h-15z"/>
              </svg>
              <span className="model-lab-tree-name">{learning.label}</span>
              <small>학습 유형</small>
            </button>
            {isExpanded(learningKey) && <div className="model-lab-tree-children">
              {learning.tasks.map(task => {
                const taskKey = learningKey + ":task:" + task.id;
                return <div key={task.id}>
                  <button type="button" className="ghost model-lab-tree-toggle"
                    aria-expanded={isExpanded(taskKey)} onClick={() => toggle(taskKey)}>
                    <span className="model-lab-tree-caret" aria-hidden="true">{isExpanded(taskKey) ? "▾" : "▸"}</span>
                    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                      <path d="M2.5 5.5h6l2 2h7v8h-15z"/>
                    </svg>
                    <span className="model-lab-tree-name">{task.label}</span>
                    <small>분석 과제</small>
                  </button>
                  {isExpanded(taskKey) && <div className="model-lab-tree-children">
                    {task.topics.map(topic => <a key={topic.id}
                      className="workspace-link model-lab-topic-link" href={hrefFor("pipelines")}
                      aria-current={active === "pipelines" ? "page" : undefined}
                      onClick={event => {
                        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                        event.preventDefault();
                        onNavigate("pipelines");
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
      </div> : group.items.filter(item =>
        (canViewDataset || item.tab === "storage" || item.tab === "audit") &&
        (item.tab !== "storage" || canInspectStorage) && (item.tab !== "audit" || canViewAudit)
      ).map(item => <a key={item.tab} href={hrefFor(item.tab)}
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
