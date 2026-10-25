import { useCallback, useEffect, useRef, useState } from "react";
import { ACTIVITY_ANOMALY_TOPIC, modelLabLabels } from "./ModelLabTaxonomy";

export type WorkspaceTab =
  | "overview" | "understanding" | "preview" | "profile" | "preparation"
  | "pipelines" | "ml" | "training" | "scoring" | "policies"
  | "evaluations" | "explanations"
  | "jobs" | "ml-jobs" | "pipeline-jobs" | "storage" | "audit";
export type MLResourceKind = "model" | "scoring" | "policy" | "evaluation" | "explanation";
export type ResourceRoute = { kind: MLResourceKind; id: string } | null;
export type MLNavigationContext = {
  datasetId?: string; modelId?: string; scoringId?: string;
  pipelineRunId?: string; pipelineReturn?: boolean; candidateRowIndex?: number;
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

function contextAt(tab: WorkspaceTab, search: string): MLNavigationContext {
  const params = new URLSearchParams(search);
  if (tab === "pipelines") {
    const runId = params.get("run") ?? "";
    return {
      datasetId: params.get("dataset") ?? "",
      pipelineRunId: runId,
      ...(runId && candidateRowAt(params) !== undefined
        ? { candidateRowIndex: candidateRowAt(params) } : {}),
      ...(runId && analysisSectionAt(params) !== undefined
        ? { pipelineAnalysisSection: analysisSectionAt(params) } : {}),
    };
  }
  if (tab === "understanding" || tab === "preparation" || tab === "preview" ||
      tab === "profile" || tab === "jobs") {
    const pipelineReturn = params.get("from") === "pipeline";
    return { pipelineReturn, datasetId: pipelineReturn ? params.get("dataset") ?? "" : "",
      ...(pipelineReturn && params.get("run") ? {
        pipelineRunId: params.get("run")!,
        ...(candidateRowAt(params) !== undefined ? { candidateRowIndex: candidateRowAt(params) } : {}),
        ...(analysisSectionAt(params) ? { pipelineAnalysisSection: analysisSectionAt(params) } : {}),
      } : {}) };
  }
  if (tab === "training" || tab === "ml") return { datasetId: params.get("dataset") ?? "" };
  if (tab === "scoring") return { modelId: params.get("model") ?? "" };
  if (isDecisionTab(tab)) return { scoringId: params.get("scoring") ?? "" };
  return {};
}

/** 동일한 Run의 Dataset 참조를 정정할 때 조사 문맥을 보존한다. 다른 Run은 새 선택이다. */
export function pipelineRunSelectionContext(
  previous: MLNavigationContext, runId: string, datasetId: string,
): MLNavigationContext {
  return {
    datasetId, pipelineRunId: runId,
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
    if (context.pipelineRunId) params.set("run", context.pipelineRunId);
    if (context.pipelineRunId && context.candidateRowIndex !== undefined)
      params.set("candidate", String(context.candidateRowIndex));
    if (context.pipelineRunId && context.pipelineAnalysisSection)
      params.set("section", context.pipelineAnalysisSection);
  }
  if ((tab === "understanding" || tab === "preparation" || tab === "preview" ||
        tab === "profile" || tab === "jobs") && context.pipelineReturn) {
    params.set("from", "pipeline");
    if (context.datasetId) params.set("dataset", context.datasetId);
    if (context.pipelineRunId) {
      params.set("run", context.pipelineRunId);
      if (context.candidateRowIndex !== undefined) params.set("candidate", String(context.candidateRowIndex));
      if (context.pipelineAnalysisSection) params.set("section", context.pipelineAnalysisSection);
    }
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
  pipelines: "/pipelines", ml: "/models", training: "/training",
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
  if (!context.pipelineReturn || !context.pipelineRunId) return path;
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
  if (params.get("from") !== "pipeline" || !params.get("run")) return {};
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
  // 미구현 설계 Preview의 과거 주소는 작동 중인 홈 기능으로 안내한다.
  if (pathname === "/research" || pathname === "/model-experience") {
    return { tab: "overview", resource: null, context: {} };
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
  if (["understanding", "preparation", "preview", "profile", "jobs"].includes(tab) &&
      ["understanding", "preparation", "preview", "profile", "jobs"].includes(previous.tab)) {
    return { pipelineReturn: previous.context.pipelineReturn,
      ...(previous.context.pipelineReturn && previous.context.datasetId
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
    if (window.location.pathname === "/research" ||
        window.location.pathname === "/model-experience") {
      window.history.replaceState(null, "", "/");
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
    const fromPipeline = (previous.tab === "pipelines" || previous.resource !== null)
      && previous.context.pipelineReturn !== false;
    const context = fromPipeline && previous.context.pipelineRunId
      ? { pipelineReturn: true, pipelineRunId: previous.context.pipelineRunId,
          datasetId: previous.context.datasetId,
          candidateRowIndex: previous.context.candidateRowIndex,
          pipelineAnalysisSection: previous.context.pipelineAnalysisSection } : {};
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
  ] },
  { title: "모델 실험실", items: [{ tab: "pipelines", label: "활동 이상 탐지" }] },
  { title: "DATA TOOLS", items: [{ tab: "preparation", label: "데이터 준비" }] },
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
      : tab === "jobs" ? ["jobs", "ml-jobs", "pipeline-jobs"].includes(active) : active === tab;
  return <nav className="global-nav platform-nav" aria-label="플랫폼 메뉴">
    {groups.map(group => <div className="platform-nav-group" key={group.title}>
      <span className="section-title">{group.title}</span>
      {group.title === "모델 실험실" && <div className="model-lab-nav-classification"
        aria-label="활동 이상 탐지 분류">
        <span>{modelLabLabels(ACTIVITY_ANOMALY_TOPIC)[0]}</span>
        <span>{modelLabLabels(ACTIVITY_ANOMALY_TOPIC)[1]}</span>
      </div>}
      {group.items.filter(item =>
        (item.tab !== "storage" || canInspectStorage) && (item.tab !== "audit" || canViewAudit)
      ).map(item => <a key={item.tab} href={paths[item.tab]}
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
