import { useEffect, useState, type ReactNode } from "react";
import type { Identity } from "./types";
import { InstanceBrand } from "./InstanceBrand";
import { PlatformNavigation, ModelAreaNavigation, isModelsArea,
  type WorkspaceTab, type MLNavigationContext, type ResourceRoute } from "./PlatformNavigation";

const pageLabels: Record<WorkspaceTab, { eyebrow: string; title: string; description: string }> = {
  overview: { eyebrow: "WORKSPACE", title: "Workspace", description: "데이터 탐색 · 활동 이상 탐지 · 이전 분석 이어하기" },
  understanding: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preview: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  profile: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "Dataset · Profile · Exploration" },
  preparation: { eyebrow: "DATA PREPARATION", title: "데이터 준비", description: "Dataset · Recipe · Transform" },
  pipelines: { eyebrow: "모델 실험실", title: "활동 이상 탐지", description: "활동 패턴을 이해하고, 모델을 사용하거나 직접 학습합니다." },
  ml: { eyebrow: "MODELS", title: "Model Revisions", description: "불변 모델 판본 · 학습 이력 · 비교" },
  training: { eyebrow: "TRAINING", title: "모델 학습", description: "Dataset · Feature · Training Job" },
  scoring: { eyebrow: "SCORING", title: "Scoring Results", description: "모델 판본 · 점수화 · 결과 조회" },
  policies: { eyebrow: "DECISION POLICY", title: "판정 정책", description: "점수 결과 · 정책 판본 · 재사용" },
  evaluations: { eyebrow: "EVALUATION", title: "평가 결과", description: "평가 근거 · 레이블 · 지표" },
  explanations: { eyebrow: "EXPLANATION", title: "점수 설명", description: "관측 결과 · 국소 민감도" },
  jobs: { eyebrow: "ACTIVITY & JOBS", title: "작업 이력", description: "Data · Exploration · ML 실행 현황" },
  "ml-jobs": { eyebrow: "ACTIVITY & JOBS", title: "ML 작업 이력", description: "학습 · Scoring · 정책 · 평가 · 설명" },
  "pipeline-jobs": { eyebrow: "ACTIVITY & JOBS", title: "모델 실험실 분석 이력", description: "이전에 수행한 모델 학습과 이상 탐지 결과" },
  storage: { eyebrow: "PLATFORM OPERATIONS", title: "저장소 상태", description: "Storage · Health" },
  audit: { eyebrow: "PLATFORM OPERATIONS", title: "보안 감사", description: "Identity · Audit" },
};

/** 모든 페이지에 공통인 탐색·인증 문맥을 업무 화면에서 분리한다. */
export function PlatformShell({ actor, section, onNavigate, onSignOut, canConfigure,
  brandName, brandReady, brandLoadError, onBrandChange, onBrandRetry,
  canInspectStorage, canViewAudit, modelContext, modelResource, dataSidebar, children }: {
  actor: Identity;
  section: WorkspaceTab;
  modelContext: MLNavigationContext;
  modelResource: ResourceRoute;
  onNavigate: (tab: WorkspaceTab) => void;
  onSignOut: () => void;
  canConfigure: boolean;
  brandName: string;
  brandReady: boolean;
  brandLoadError: string;
  onBrandChange: (name: string) => void;
  onBrandRetry: () => void;
  canInspectStorage: boolean;
  canViewAudit: boolean;
  dataSidebar?: ReactNode;
  children: ReactNode;
}) {
  const [localOperatorHost, setLocalOperatorHost] = useState(false);
  useEffect(() => {
    setLocalOperatorHost(["localhost", "127.0.0.1", "::1"].includes(window.location.hostname));
  }, []);
  const label = pageLabels[section];
  const title = section === "overview" ? `${brandName} Workspace` : label.title;
  useEffect(() => {
    document.title = section === "overview" ? title : `${title} · ${brandName}`;
  }, [section, title, brandName]);
  return <div className="shell">
    <aside className="sidebar">
      <InstanceBrand canEdit={canConfigure} brandName={brandName}
        brandReady={brandReady} brandLoadError={brandLoadError} onBrandChange={onBrandChange}
        onBrandRetry={onBrandRetry} />
      <PlatformNavigation active={section} onNavigate={onNavigate}
        canInspectStorage={canInspectStorage} canViewAudit={canViewAudit} />
      {dataSidebar}
      {localOperatorHost && (canInspectStorage || canViewAudit) && <div className="operator-tools">
        <span className="eyebrow">로컬 운영 도구</span>
        <a href="http://localhost:9607" target="_blank" rel="noopener noreferrer">
          Dagster Workflow · 실행 기록 ↗
        </a>
        <small>현재 PC에서만 접속할 수 있습니다. SOS 로그인과 별도인 관리자용 UI입니다.</small>
      </div>}
      <div className="sidebar-bottom">
        <a href="/docs" target="_blank" rel="noreferrer">↗ Scalar API Reference</a>
      </div>
    </aside>
    <main className="main">
      <header className="workspace-header"><div>
        <span className="eyebrow">{label.eyebrow}</span>
        <h1 id="sos-page-heading" tabIndex={-1}>{title}</h1>
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
      <footer>{brandName} · {label.eyebrow}<span>Dataset · Job · Artifact</span></footer>
    </main>
  </div>;
}
