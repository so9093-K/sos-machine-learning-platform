import type { ReactNode } from "react";
import type { Identity } from "./types";
import { InstanceBrand } from "./InstanceBrand";
import { PlatformNavigation, type WorkspaceTab } from "./PlatformNavigation";

const pageLabels: Record<WorkspaceTab, { eyebrow: string; title: string; description: string }> = {
  overview: { eyebrow: "WORKSPACE", title: "Workspace Overview", description: "Data · Research · Models · Operations" },
  understanding: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preview: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  profile: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preparation: { eyebrow: "DATA PREPARATION", title: "데이터 준비", description: "Dataset · Recipe · Transform" },
  research: { eyebrow: "RESEARCH", title: "Research & Experiments", description: "연구·실험 경험 설계" },
  ml: { eyebrow: "MODELS", title: "모델·학습", description: "Model Revision · Training · Scoring" },
  experience: { eyebrow: "MODEL EXPERIENCE", title: "Model Experience", description: "모델 활용 경험 설계" },
  jobs: { eyebrow: "ACTIVITY & JOBS", title: "데이터 작업 이력", description: "Data Jobs · Exploration Jobs" },
  storage: { eyebrow: "PLATFORM OPERATIONS", title: "저장소 상태", description: "Storage · Health" },
  audit: { eyebrow: "PLATFORM OPERATIONS", title: "보안 감사", description: "Identity · Audit" },
};

/** 모든 페이지에 공통인 탐색·인증 문맥을 업무 화면에서 분리한다. */
export function PlatformShell({ actor, section, onNavigate, onSignOut, canConfigure,
  canInspectStorage, canViewAudit, dataSidebar, children }: {
  actor: Identity;
  section: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
  onSignOut: () => void;
  canConfigure: boolean;
  canInspectStorage: boolean;
  canViewAudit: boolean;
  dataSidebar?: ReactNode;
  children: ReactNode;
}) {
  const label = pageLabels[section];
  return <div className="shell">
    <aside className="sidebar">
      <InstanceBrand canEdit={canConfigure} />
      <PlatformNavigation active={section} onNavigate={onNavigate}
        canInspectStorage={canInspectStorage} canViewAudit={canViewAudit} />
      {dataSidebar}
      <div className="sidebar-bottom">
        <a href="/docs" target="_blank" rel="noreferrer">↗ Scalar API Reference</a>
      </div>
    </aside>
    <main className="main">
      <header className="workspace-header"><div>
        <span className="eyebrow">{label.eyebrow}</span>
        <h1>{label.title}</h1>
        <p>{label.description}</p>
      </div>
        <div className="header-meta">
          <span className="environment">{actor.mode === "development" ? "로컬 인증" : "토큰 인증"}</span>
          <div className="auth-status">
            <span>{actor.actor_id} · {actor.roles.join(", ")}</span>
            {actor.mode !== "development" &&
              <button type="button" className="ghost" onClick={onSignOut}>인증 해제</button>}
          </div>
        </div>
      </header>
      {children}
      <footer>SOS · {label.eyebrow}<span>Dataset · Job · Artifact</span></footer>
    </main>
  </div>;
}
