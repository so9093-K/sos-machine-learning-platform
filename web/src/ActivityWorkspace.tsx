import type { ReactNode } from "react";
import { MLActivityPanel } from "./MLActivityPanel";
import { PipelineHistoryPanel } from "./PipelineHistoryPanel";
import type { Dataset } from "./types";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

export type ActivityView = "data" | "ml" | "pipeline";

/** Job API는 종류별로 분리되어 있다. 화면도 조회 범위를 숨기지 않고 작업 유형을 전환한다. */
export function ActivityWorkspace({ active, onChange, onNavigate, onOpenResource, onOpenPipelineRun, datasets, canCancel, children }: {
  active: ActivityView;
  onChange: (view: ActivityView) => void;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onOpenPipelineRun: (runId: string, datasetId: string) => void;
  datasets: Dataset[];
  canCancel: boolean;
  children: ReactNode;
}) {
  return <div className="platform-activity">
    <section className="platform-section-intro">
      <h2>실행 이력과 결과</h2>
      <p>데이터·탐색, 독립 ML 실행, 모델 실험실의 분석 결과를 확인합니다.
        각 목록은 실제 조회된 범위만 표시합니다.</p>
    </section>
    <nav className="activity-scope-nav" aria-label="작업 이력 종류">
      <button type="button" aria-current={active === "data" ? "page" : undefined}
        className={active === "data" ? "" : "secondary"} onClick={() => onChange("data")}>
        데이터·탐색 작업
      </button>
      <button type="button" aria-current={active === "ml" ? "page" : undefined}
        className={active === "ml" ? "" : "secondary"} onClick={() => onChange("ml")}>
        ML 실행 작업
      </button>
      <button type="button" aria-current={active === "pipeline" ? "page" : undefined}
        className={active === "pipeline" ? "" : "secondary"} onClick={() => onChange("pipeline")}>
        모델 실험실 분석
      </button>
    </nav>
    {active === "data" ? children : active === "ml" ?
      <MLActivityPanel onNavigate={onNavigate} onOpenResource={onOpenResource} canCancel={canCancel}
        datasets={datasets} /> :
      <PipelineHistoryPanel datasets={datasets} onOpenRun={onOpenPipelineRun}
        onStart={() => onNavigate("pipelines")} />}
  </div>;
}
