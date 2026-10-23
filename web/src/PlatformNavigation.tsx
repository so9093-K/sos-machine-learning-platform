import { useCallback, useEffect, useRef, useState } from "react";

export type WorkspaceTab =
  | "overview" | "understanding" | "preview" | "profile" | "preparation"
  | "research" | "pipelines" | "ml" | "training" | "scoring" | "policies"
  | "evaluations" | "explanations" | "experience"
  | "jobs" | "ml-jobs" | "storage" | "audit";
export type MLResourceKind = "model" | "scoring" | "policy" | "evaluation" | "explanation";
export type ResourceRoute = { kind: MLResourceKind; id: string } | null;
export type MLNavigationContext = {
  datasetId?: string; modelId?: string; scoringId?: string;
  pipelineRunId?: string; pipelineReturn?: boolean;
};
type WorkspaceRoute = {
  tab: WorkspaceTab; resource: ResourceRoute; context: MLNavigationContext;
};

function isDecisionTab(tab: WorkspaceTab): boolean {
  return tab === "policies" || tab === "evaluations" || tab === "explanations";
}

function contextAt(tab: WorkspaceTab, search: string): MLNavigationContext {
  const params = new URLSearchParams(search);
  if (tab === "pipelines") return {
    datasetId: params.get("dataset") ?? "",
    pipelineRunId: params.get("run") ?? "",
  };
  if (tab === "understanding" || tab === "preparation" || tab === "preview" || tab === "profile") {
    const pipelineReturn = params.get("from") === "pipeline";
    return { pipelineReturn, datasetId: pipelineReturn ? params.get("dataset") ?? "" : "" };
  }
  if (tab === "training" || tab === "ml") return { datasetId: params.get("dataset") ?? "" };
  if (tab === "scoring") return { modelId: params.get("model") ?? "" };
  if (isDecisionTab(tab)) return { scoringId: params.get("scoring") ?? "" };
  return {};
}

export function contextPath(tab: WorkspaceTab, context: MLNavigationContext): string {
  const params = new URLSearchParams();
  if (tab === "pipelines") {
    if (context.datasetId) params.set("dataset", context.datasetId);
    if (context.pipelineRunId) params.set("run", context.pipelineRunId);
  }
  if ((tab === "understanding" || tab === "preparation" || tab === "preview" || tab === "profile")
      && context.pipelineReturn) {
    params.set("from", "pipeline");
    if (context.datasetId) params.set("dataset", context.datasetId);
  }
  if ((tab === "training" || tab === "ml") && context.datasetId) {
    params.set("dataset", context.datasetId);
  }
  if (tab === "scoring" && context.modelId) params.set("model", context.modelId);
  if (isDecisionTab(tab) && context.scoringId) params.set("scoring", context.scoringId);
  return paths[tab] + (params.size ? "?" + params.toString() : "");
}

const paths: Record<WorkspaceTab, string> = {
  overview: "/", understanding: "/data", preview: "/data/query",
  profile: "/data/profile", preparation: "/data/prepare",
  research: "/research", pipelines: "/pipelines", ml: "/models", training: "/training",
  scoring: "/scoring", policies: "/policies",
  evaluations: "/evaluations", explanations: "/explanations",
  experience: "/model-experience",
  jobs: "/activity/jobs", "ml-jobs": "/activity/ml", storage: "/operations/storage", audit: "/operations/audit",
};
const resourcePaths: Record<MLResourceKind, { base: string; tab: WorkspaceTab }> = {
  model: { base: "/models", tab: "ml" },
  scoring: { base: "/scoring/results", tab: "scoring" },
  policy: { base: "/policies", tab: "policies" },
  evaluation: { base: "/evaluations", tab: "evaluations" },
  explanation: { base: "/explanations", tab: "explanations" },
};
export function resourcePath(kind: MLResourceKind, id: string): string {
  return `${resourcePaths[kind].base}/${encodeURIComponent(id)}`;
}
export function routeAt(pathname: string, search: string): WorkspaceRoute {
  for (const [kind, config] of Object.entries(resourcePaths) as
      Array<[MLResourceKind, { base: string; tab: WorkspaceTab }]>) {
    if (pathname.startsWith(config.base + "/")) {
      const id = pathname.slice(config.base.length + 1);
      if (id && !id.includes("/")) {
        try { return { tab: config.tab, resource: { kind, id: decodeURIComponent(id) }, context: {} }; }
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
  // 결과 상세에서 모델 영역 탭을 직접 선택해도 이미 확인한 리소스를 전달한다.
  if (previous.resource?.kind === "scoring" && isDecisionTab(tab)) {
    return { scoringId: previous.resource.id };
  }
  if (previous.resource?.kind === "model" && tab === "scoring") {
    return { modelId: previous.resource.id };
  }
  if (["understanding", "preparation", "preview", "profile"].includes(tab) &&
      ["understanding", "preparation", "preview", "profile"].includes(previous.tab)) {
    return { pipelineReturn: previous.context.pipelineReturn,
      ...(previous.context.pipelineReturn && previous.context.datasetId
        ? { datasetId: previous.context.datasetId } : {}) };
  }
  if (isDecisionTab(tab) && isDecisionTab(previous.tab)) {
    return { scoringId: previous.context.scoringId };
  }
  if ((tab === "training" || tab === "ml") &&
      (previous.tab === "training" || previous.tab === "ml")) {
    return { datasetId: previous.context.datasetId };
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
    update({ tab: resourcePaths[kind].tab, resource: { kind, id }, context: {} },
      resourcePath(kind, id));
  }, [update]);
  return { tab: route.tab, resource: route.resource, context: route.context,
    navigate, navigateWithContext, selectContext, openResource };
}

type NavItem = { tab: WorkspaceTab; label: string; concept?: boolean };
const groups: ReadonlyArray<{ title: string; items: NavItem[] }> = [
  { title: "WORKSPACE", items: [{ tab: "overview", label: "개요" }] },
  { title: "DATA", items: [
    { tab: "understanding", label: "데이터 탐색" },
    { tab: "preparation", label: "데이터 준비" },
  ] },
  { title: "RESEARCH", items: [{ tab: "research", label: "연구·실험", concept: true }] },
  { title: "PIPELINES", items: [{ tab: "pipelines", label: "등록형 분석 Pipeline" }] },
  { title: "MODELS", items: [{ tab: "ml", label: "모델과 실행" }] },
  { title: "MODEL EXPERIENCE", items: [{ tab: "experience", label: "모델 활용", concept: true }] },
  { title: "ACTIVITY & OPERATIONS", items: [
    { tab: "jobs", label: "작업 이력" },
    { tab: "storage", label: "저장소 상태" },
    { tab: "audit", label: "보안 감사" },
  ] },
];

const modelTabs: ReadonlyArray<{ tab: WorkspaceTab; label: string; group: string }> = [
  { tab: "ml", label: "모델 판본", group: "리소스" },
  { tab: "training", label: "학습", group: "실행" },
  { tab: "scoring", label: "점수화", group: "실행" },
  { tab: "evaluations", label: "평가", group: "결과와 판단" },
  { tab: "policies", label: "판정 정책", group: "결과와 판단" },
  { tab: "explanations", label: "설명", group: "결과와 판단" },
];
export function isModelsArea(tab: WorkspaceTab): boolean {
  return modelTabs.some(item => item.tab === tab);
}

/** 전역 내비게이션이 아니라 모델 리소스 문맥 안에서 독립 기능을 탐색한다. */
export function ModelAreaNavigation({ active, context, resource, onNavigate }: {
  active: WorkspaceTab;
  context: MLNavigationContext;
  resource: ResourceRoute;
  onNavigate: (tab: WorkspaceTab) => void;
}) {
  return <nav className="model-area-nav" aria-label="모델 관련 화면">
    {modelTabs.map(item => <a key={item.tab}
      href={contextPath(item.tab, nextNavigationContext(
        { tab: active, resource, context }, item.tab))}
      aria-current={active === item.tab ? "page" : undefined}
      className="model-area-link"
      onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onNavigate(item.tab);
      }}>
      <small>{item.group}</small><span>{item.label}</span>
    </a>)}
  </nav>;
}

/** 영역별 진입점은 내부 결과가 없어도 언제나 탐색 가능하다. */
export function PlatformNavigation({ active, onNavigate, canInspectStorage, canViewAudit }: {
  active: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
  canInspectStorage: boolean;
  canViewAudit: boolean;
}) {
  const isCurrent = (tab: WorkspaceTab) =>
    tab === "understanding" ? ["understanding", "preview", "profile"].includes(active)
      : tab === "ml" ? isModelsArea(active)
      : tab === "jobs" ? ["jobs", "ml-jobs"].includes(active) : active === tab;
  return <nav className="global-nav platform-nav" aria-label="플랫폼 메뉴">
    {groups.map(group => <div className="platform-nav-group" key={group.title}>
      <span className="section-title">{group.title}</span>
      {group.items.filter(item =>
        (item.tab !== "storage" || canInspectStorage) && (item.tab !== "audit" || canViewAudit)
      ).map(item => <a key={item.tab} href={paths[item.tab]}
        className="workspace-link" aria-current={isCurrent(item.tab) ? "page" : undefined}
        onClick={event => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onNavigate(item.tab);
        }}>
        <span>{item.label}</span>{item.concept && <small>설계</small>}
      </a>)}
    </div>)}
  </nav>;
}
