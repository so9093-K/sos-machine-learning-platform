import { useCallback, useEffect, useRef, useState } from "react";

export type WorkspaceTab =
  | "overview" | "understanding" | "preview" | "profile" | "preparation"
  | "research" | "ml" | "experience" | "jobs" | "storage" | "audit";

const paths: Record<WorkspaceTab, string> = {
  overview: "/", understanding: "/data", preview: "/data/query",
  profile: "/data/profile", preparation: "/data/prepare",
  research: "/research", ml: "/models", experience: "/model-experience",
  jobs: "/activity/jobs", storage: "/operations/storage", audit: "/operations/audit",
};

function tabAt(pathname: string): WorkspaceTab {
  return (Object.keys(paths) as WorkspaceTab[]).find(tab => paths[tab] === pathname) ?? "overview";
}

/** 페이지 주소를 사용자 내비게이션 상태와 일치시킨다. */
export function useWorkspaceNavigation() {
  const [tab, setActive] = useState<WorkspaceTab>(() => tabAt(window.location.pathname));
  const current = useRef(tab);
  useEffect(() => {
    const sync = () => {
      current.current = tabAt(window.location.pathname);
      setActive(current.current);
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const navigate = useCallback((target: WorkspaceTab | ((previous: WorkspaceTab) => WorkspaceTab)) => {
    const next = typeof target === "function" ? target(current.current) : target;
    if (current.current !== next || window.location.pathname !== paths[next]) {
      window.history.pushState(null, "", paths[next]);
    }
    current.current = next;
    setActive(next);
  }, []);
  return { tab, navigate };
}

type NavItem = { tab: WorkspaceTab; label: string; concept?: boolean };
const groups: ReadonlyArray<{ title: string; items: NavItem[] }> = [
  { title: "WORKSPACE", items: [{ tab: "overview", label: "개요" }] },
  { title: "DATA", items: [
    { tab: "understanding", label: "데이터 탐색" },
    { tab: "preparation", label: "데이터 준비" },
  ] },
  { title: "RESEARCH & MODELS", items: [
    { tab: "research", label: "연구·실험", concept: true },
    { tab: "ml", label: "모델·학습" },
    { tab: "experience", label: "모델 활용", concept: true },
  ] },
  { title: "ACTIVITY & OPERATIONS", items: [
    { tab: "jobs", label: "데이터 작업 이력" },
    { tab: "storage", label: "저장소 상태" },
    { tab: "audit", label: "보안 감사" },
  ] },
];

/** 제품 경험의 영역은 실행 파이프라인과 독립적으로 탐색한다. */
export function PlatformNavigation({ active, onNavigate, canInspectStorage, canViewAudit }: {
  active: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
  canInspectStorage: boolean;
  canViewAudit: boolean;
}) {
  const isCurrent = (tab: WorkspaceTab) =>
    tab === "understanding" ? ["understanding", "preview", "profile"].includes(active) : active === tab;
  return <nav className="global-nav platform-nav" aria-label="플랫폼 메뉴">
    {groups.map(group => <div className="platform-nav-group" key={group.title}>
      <span className="section-title">{group.title}</span>
      {group.items.filter(item =>
        (item.tab !== "storage" || canInspectStorage) && (item.tab !== "audit" || canViewAudit)
      ).map(item => <button key={item.tab} type="button"
        className="workspace-link ghost" aria-current={isCurrent(item.tab) ? "page" : undefined}
        onClick={() => onNavigate(item.tab)}>
        <span>{item.label}</span>{item.concept && <small>설계</small>}
      </button>)}
    </div>)}
  </nav>;
}
