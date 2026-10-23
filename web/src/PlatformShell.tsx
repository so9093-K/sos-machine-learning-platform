import { useEffect, type ReactNode } from "react";
import type { Identity } from "./types";
import { InstanceBrand } from "./InstanceBrand";
import { PlatformNavigation, ModelAreaNavigation, isModelsArea,
  type WorkspaceTab, type MLNavigationContext, type ResourceRoute } from "./PlatformNavigation";

const pageLabels: Record<WorkspaceTab, { eyebrow: string; title: string; description: string }> = {
  overview: { eyebrow: "WORKSPACE", title: "Workspace Overview", description: "Data · Research · Models · Operations" },
  understanding: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preview: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  profile: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preparation: { eyebrow: "DATA PREPARATION", title: "데이터 준비", description: "Dataset · Recipe · Transform" },
  pipelines: { eyebrow: "PIPELINES", title: "등록형 ML Pipeline", description: "등록된 분석 방법을 내 데이터로 실행합니다." },
  ml: { eyebrow: "MODELS", title: "Model Revisions", description: "불변 모델 판본 · 학습 이력 · 비교" },
  training: { eyebrow: "TRAINING", title: "모델 학습", description: "Dataset · Feature · Training Job" },
  scoring: { eyebrow: "SCORING", title: "Scoring Results", description: "모델 판본 · 점수화 · 결과 조회" },
  policies: { eyebrow: "DECISION POLICY", title: "판정 정책", description: "점수 결과 · 정책 판본 · 재사용" },
  evaluations: { eyebrow: "EVALUATION", title: "평가 결과", description: "평가 근거 · 레이블 · 지표" },
  explanations: { eyebrow: "EXPLANATION", title: "점수 설명", description: "관측 결과 · 국소 민감도" },
  jobs: { eyebrow: "ACTIVITY & JOBS", title: "작업 이력", description: "Data · Exploration · ML 실행 현황" },
  "ml-jobs": { eyebrow: "ACTIVITY & JOBS", title: "ML 작업 이력", description: "학습 · Scoring · 정책 · 평가 · 설명" },
  storage: { eyebrow: "PLATFORM OPERATIONS", title: "저장소 상태", description: "Storage · Health" },
  audit: { eyebrow: "PLATFORM OPERATIONS", title: "보안 감사", description: "Identity · Audit" },
};

/** 모든 페이지에 공통인 탐색·인증 문맥을 업무 화면에서 분리한다. */
export function PlatformShell({ actor, section, onNavigate, onSignOut, canConfigure,
  canInspectStorage, canViewAudit, modelContext, modelResource, dataSidebar, children }: {
  actor: Identity;
  section: WorkspaceTab;
  modelContext: MLNavigationContext;
  modelResource: ResourceRoute;
  onNavigate: (tab: WorkspaceTab) => void;
  onSignOut: () => void;
  canConfigure: boolean;
  canInspectStorage: boolean;
  canViewAudit: boolean;
  dataSidebar?: ReactNode;
  children: ReactNode;
}) {
  const label = pageLabels[section];
  useEffect(() => { document.title = `${label.title} · SOS`; }, [label.title]);
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
        <h1 id="sos-page-heading" tabIndex={-1}>{label.title}</h1>
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
      {isModelsArea(section) && <ModelAreaNavigation active={section}
        context={modelContext} resource={modelResource} onNavigate={onNavigate} />}
      {children}
      <footer>SOS · {label.eyebrow}<span>Dataset · Job · Artifact</span></footer>
    </main>
  </div>;
}
