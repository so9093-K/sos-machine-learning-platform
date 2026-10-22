import type { ReactNode } from "react";
import { MLActivityPanel } from "./MLActivityPanel";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

export type ActivityView = "data" | "ml";

/** Job API는 종류별로 분리되어 있다. 화면도 조회 범위를 숨기지 않고 작업 유형을 전환한다. */
export function ActivityWorkspace({ active, onChange, onNavigate, onOpenResource, canCancel, children }: {
  active: ActivityView;
  onChange: (view: ActivityView) => void;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  canCancel: boolean;
  children: ReactNode;
}) {
  return <div className="platform-activity">
    <section className="platform-section-intro">
      <h2>실행 이력과 결과</h2>
      <p>데이터·탐색과 ML 실행을 같은 위치에서 확인합니다.
        서로 다른 API의 조회된 범위만 표시하며, 전체 작업 수를 추정하지 않습니다.</p>
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
    </nav>
    {active === "data" ? children :
      <MLActivityPanel onNavigate={onNavigate} onOpenResource={onOpenResource} canCancel={canCancel} />}
  </div>;
}
