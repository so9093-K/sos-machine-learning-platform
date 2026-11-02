import type { ReactNode } from "react";
import { MLActivityPanel } from "./MLActivityPanel";
import type { Dataset } from "./types";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

export type ActivityView = "data" | "ml";

/** 데이터 작업과 모델 적용 작업을 작업 맥락별로 나눠 보인다. 조회·취소는 모두 공통 Job API를 쓴다. */
export function ActivityWorkspace({ active, onChange, onNavigate, onOpenResource, datasets, canCancel, children }: {
  active: ActivityView;
  onChange: (view: ActivityView) => void;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  datasets: Dataset[];
  canCancel: boolean;
  children: ReactNode;
}) {
  return <div className="platform-activity">
    <nav className="activity-scope-nav" aria-label="작업 이력 종류">
      <button type="button" aria-current={active === "data" ? "page" : undefined}
        className={active === "data" ? "" : "secondary"} onClick={() => onChange("data")}>
        데이터·탐색 작업
      </button>
      <button type="button" aria-current={active === "ml" ? "page" : undefined}
        className={active === "ml" ? "" : "secondary"} onClick={() => onChange("ml")}>
        모델 적용 작업
      </button>
    </nav>
    {active === "data" ? children :
      <MLActivityPanel onNavigate={onNavigate} onOpenResource={onOpenResource} canCancel={canCancel}
        datasets={datasets} />}
  </div>;
}
