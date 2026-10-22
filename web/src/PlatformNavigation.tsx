import { useCallback, useEffect, useRef, useState } from "react";

export type WorkspaceTab =
  | "overview" | "understanding" | "preview" | "profile" | "preparation"
  | "research" | "ml" | "training" | "scoring" | "policies"
  | "evaluations" | "explanations" | "experience"
  | "jobs" | "ml-jobs" | "storage" | "audit";
export type MLResourceKind = "model" | "scoring" | "policy" | "evaluation" | "explanation";
export type ResourceRoute = { kind: MLResourceKind; id: string } | null;

const paths: Record<WorkspaceTab, string> = {
  overview: "/", understanding: "/data", preview: "/data/query",
  profile: "/data/profile", preparation: "/data/prepare",
  research: "/research", ml: "/models", training: "/training",
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
function routeAt(pathname: string): { tab: WorkspaceTab; resource: ResourceRoute } {
  for (const [kind, config] of Object.entries(resourcePaths) as
      Array<[MLResourceKind, { base: string; tab: WorkspaceTab }]>) {
    if (pathname.startsWith(config.base + "/")) {
      const id = pathname.slice(config.base.length + 1);
      if (id && !id.includes("/")) {
        try { return { tab: config.tab, resource: { kind, id: decodeURIComponent(id) } }; }
        catch { break; }
      }
    }
  }
  const tab = (Object.keys(paths) as WorkspaceTab[]).find(key => paths[key] === pathname);
  return { tab: tab ?? "overview", resource: null };
}

/** 탭과 불변 결과 주소를 같은 탐색 상태로 유지한다. */
export function useWorkspaceNavigation() {
  const [route, setRoute] = useState(() => routeAt(window.location.pathname));
  const current = useRef(route);
  useEffect(() => {
    const sync = () => {
      current.current = routeAt(window.location.pathname);
      setRoute(current.current);
      requestAnimationFrame(() => document.getElementById("sos-page-heading")?.focus());
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const update = useCallback((next: { tab: WorkspaceTab; resource: ResourceRoute }, path: string) => {
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
    current.current = next;
    setRoute(next);
    requestAnimationFrame(() => document.getElementById("sos-page-heading")?.focus());
  }, []);
  const navigate = useCallback((target: WorkspaceTab | ((previous: WorkspaceTab) => WorkspaceTab)) => {
    const tab = typeof target === "function" ? target(current.current.tab) : target;
    update({ tab, resource: null }, paths[tab]);
  }, [update]);
  const openResource = useCallback((kind: MLResourceKind, id: string) => {
    if (!id) return;
    update({ tab: resourcePaths[kind].tab, resource: { kind, id } }, resourcePath(kind, id));
  }, [update]);
  return { tab: route.tab, resource: route.resource, navigate, openResource };
}

type NavItem = { tab: WorkspaceTab; label: string; concept?: boolean };
const groups: ReadonlyArray<{ title: string; items: NavItem[] }> = [
  { title: "WORKSPACE", items: [{ tab: "overview", label: "개요" }] },
  { title: "DATA", items: [
    { tab: "understanding", label: "데이터 탐색" },
    { tab: "preparation", label: "데이터 준비" },
  ] },
  { title: "RESEARCH", items: [{ tab: "research", label: "연구·실험", concept: true }] },
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
export function ModelAreaNavigation({ active, onNavigate }: {
  active: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
}) {
  return <nav className="model-area-nav" aria-label="모델 관련 화면">
    {modelTabs.map(item => <a key={item.tab} href={paths[item.tab]}
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
