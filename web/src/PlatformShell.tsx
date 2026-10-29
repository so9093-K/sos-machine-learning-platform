import { useEffect, useState, type ReactNode } from "react";
import type { Identity } from "./types";
import { InstanceBrand } from "./InstanceBrand";
import { dagsterUrl, feastUrl, isLocalOperatorHost, mlflowUrl } from "./operator-tools";
import { PlatformNavigation, ModelAreaNavigation, isModelsArea,
  type WorkspaceTab, type MLNavigationContext, type ResourceRoute } from "./PlatformNavigation";

const pageLabels: Record<WorkspaceTab, { eyebrow: string; title: string; description: string }> = {
  overview: { eyebrow: "WORKSPACE", title: "Workspace", description: "데이터 탐색 · 모델 활용 · 실행 근거" },
  understanding: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "구조·품질 이해 · 조건 조회 · 분포 탐색" },
  preview: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "구조·품질 이해 · 조건 조회 · 분포 탐색" },
  profile: { eyebrow: "DATA EXPLORER", title: "데이터 탐색", description: "구조·품질 이해 · 조건 조회 · 분포 탐색" },
  preparation: { eyebrow: "DATA PREPARATION", title: "데이터 준비", description: "원본 선택 · 변환 편집 · 결과 Dataset" },
  topic: { eyebrow: "분석 주제", title: "활동 이상 탐지", description: "활동 패턴 이해 · 모델 적용 · 결과 분석" },
  pipelines: { eyebrow: "MODEL OPERATIONS", title: "등록 파이프라인", description: "모델 공급 운영 · 단계별 결과 · 실행 근거" },
  ml: { eyebrow: "MODELS", title: "모델 활용", description: "모델 선택 · 입력 확인 · 적용 · 결과 이해" },
  scoring: { eyebrow: "SCORING", title: "모델 적용", description: "Dataset 분석 · 직접 입력 · 점수 결과" },
  policies: { eyebrow: "DECISION POLICY", title: "판정 정책", description: "점수 결과 · 정책 판본 · 재사용" },
  evaluations: { eyebrow: "EVALUATION", title: "평가 결과", description: "평가 근거 · 레이블 · 지표" },
  explanations: { eyebrow: "EXPLANATION", title: "점수 설명", description: "관측값 · 점수 기여도 · 기준값 치환" },
  jobs: { eyebrow: "ACTIVITY & JOBS", title: "작업 이력", description: "Data · Exploration · ML 실행 현황" },
  "ml-jobs": { eyebrow: "ACTIVITY & JOBS", title: "ML 작업 이력", description: "점수 계산 · 정책 · 평가 · 설명" },
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
  // 좁은 화면에서는 메뉴가 업무 화면을 밀어내지 않도록 접어 두고, 화면 이동 후 다시 접는다.
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => { setMenuOpen(false); }, [section, modelContext.datasetId, modelContext.modelId, modelContext.dataJobId, modelContext.pipelineRunId, modelResource?.id]);
  useEffect(() => {
    setLocalOperatorHost(isLocalOperatorHost());
  }, []);
  const label = pageLabels[section];
  const title = section === "overview" ? `${brandName} Workspace` : label.title;
  useEffect(() => {
    document.title = section === "overview" ? title : `${title} · ${brandName}`;
  }, [section, title, brandName]);
  return <div className="shell">
    <aside className="sidebar">
      <div className="sidebar-top">
        <InstanceBrand canEdit={canConfigure} brandName={brandName}
          brandReady={brandReady} brandLoadError={brandLoadError} onBrandChange={onBrandChange}
          onBrandRetry={onBrandRetry} />
        <button type="button" className="secondary sidebar-menu-toggle" aria-expanded={menuOpen}
          aria-controls="sos-sidebar-menu" onClick={() => setMenuOpen(open => !open)}>
          {menuOpen ? "메뉴 닫기" : "메뉴"}
        </button>
      </div>
      <div id="sos-sidebar-menu" className={"sidebar-menu" + (menuOpen ? " open" : "")}>
      <PlatformNavigation active={section} context={modelContext} resource={modelResource} onNavigate={onNavigate}
        canInspectStorage={canInspectStorage} canViewAudit={canViewAudit}
        canViewDataset={actor.capabilities.includes("dataset.read")}
        canPrepareData={actor.capabilities.includes("job.create")}
        canOperatePipelines={actor.capabilities.includes("model.train") && actor.capabilities.includes("dataset.read")} />
      {dataSidebar}
      {localOperatorHost && (canInspectStorage || canViewAudit) && <div className="operator-tools">
        <span className="eyebrow">로컬 운영 도구</span>
        <a href={dagsterUrl()} target="_blank" rel="noopener noreferrer">
          Dagster · Workflow Run/실행 로그 ↗
        </a>
        <a href={mlflowUrl()} target="_blank" rel="noopener noreferrer">
          MLflow · 연구 Run/모델 Registry ↗
        </a>
        <a href={feastUrl()} target="_blank" rel="noopener noreferrer">
          Feast · Feature Catalog/Lineage ↗
        </a>
        <small>운영자용 별도 관리 UI입니다. 같은 PC에서만 접속할 수 있으며
          SOS 로그인 권한이 이 도구에 적용되는 것은 아닙니다.</small>
      </div>}
      </div>
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
            <span>{actor.actor_id} · {actor.roles.map(role => ({ administrator: "관리자", researcher: "분석 사용자", viewer: "조회 사용자", operator: "운영자" }[role] ?? role)).join(" · ")}</span>
            {actor.mode !== "development" &&
              <button type="button" className="ghost" onClick={onSignOut}>인증 해제</button>}
          </div>
        </div>
      </header>
      {isModelsArea(section) && <ModelAreaNavigation active={section}
        context={modelContext} resource={modelResource} onNavigate={onNavigate} />}
      {children}
      <footer>{brandName} · {label.eyebrow}<span>데이터 · 모델 · 분석 결과</span></footer>
    </main>
  </div>;
}
