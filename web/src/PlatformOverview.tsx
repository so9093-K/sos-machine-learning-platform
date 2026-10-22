import { MLActivityPanel } from "./MLActivityPanel";
import type { MLResourceKind, WorkspaceTab } from "./PlatformNavigation";

type DatasetLink = { id: string; name: string; kind: string };
type Activity = { id: string; status: string; created_at: string };
type Props = {
  datasets: DatasetLink[];
  jobs: Activity[];
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenDataset: (datasetId: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onViewMlActivity: () => void;
  onViewDataActivity: () => void;
};

/** 조회 가능한 활동·리소스로 작업을 이어서 진행하게 하며 임의의 전체 집계를 만들지 않는다. */
export function PlatformOverview({ datasets, jobs, onNavigate, onOpenDataset, onOpenResource,
  onViewMlActivity, onViewDataActivity }: Props) {
  const recentData = [...jobs].sort((a, b) =>
    b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)).slice(0, 4);
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">EXPLORE · EXPERIMENT · UNDERSTAND · EVOLVE</span>
      <h2>작업을 이어서 시작하세요</h2>
      <p>데이터를 이해하고, 기존 모델과 실행 결과를 다시 찾고, 필요할 때 새 작업을 시작합니다.
        정해진 파이프라인을 순서대로 진행할 필요는 없습니다.</p>
    </section>
    <section aria-label="최근 활동">
      <h3 className="platform-section-title">최근 활동과 결과</h3>
      <div className="platform-overview-grid">
        <MLActivityPanel compact onNavigate={onNavigate} onOpenResource={onOpenResource}
          onViewAll={onViewMlActivity}/>
        <section className="panel" aria-labelledby="recent-data-heading">
          <div className="panel-head"><div>
            <h3 id="recent-data-heading">최근 데이터 작업</h3>
            <p>현재 조회된 데이터·탐색 Job의 범위에서 표시합니다.</p>
          </div><button className="secondary" onClick={onViewDataActivity}>작업 이력 →</button></div>
          {recentData.length === 0 ? <p className="muted">조회된 데이터 작업이 없습니다.</p>
            : <div className="platform-compact-list">{recentData.map(job =>
              <button key={job.id} className="platform-list-link ghost" onClick={onViewDataActivity}>
                <span className="platform-resource-name">Job {job.id.slice(0, 12)}</span>
                <span className={`status ${job.status.toLowerCase()}`}>{job.status}</span>
              </button>)}</div>}
        </section>
      </div>
    </section>
    <section className="panel" aria-labelledby="known-datasets-heading">
      <div className="panel-head"><div>
        <h3 id="known-datasets-heading">Dataset</h3>
        <p>현재 조회된 Dataset 목록입니다. 선택하면 데이터 이해 화면으로 이동합니다.</p>
      </div><button className="secondary" onClick={() => onNavigate("understanding")}>데이터 탐색 →</button></div>
      {datasets.length === 0
        ? <p className="muted">조회된 Dataset이 없습니다. 데이터 탐색에서 등록하거나 연결할 수 있습니다.</p>
        : <div className="platform-compact-list platform-dataset-list">
          {datasets.slice(0, 5).map(dataset => <button className="platform-list-link ghost"
            key={dataset.id} onClick={() => onOpenDataset(dataset.id)}>
            <span className="platform-resource-name">{dataset.name}</span><small>{dataset.kind}</small>
          </button>)}
        </div>}
    </section>
    <section aria-labelledby="new-work-heading">
      <h3 className="platform-section-title" id="new-work-heading">새 작업 시작</h3>
      <div className="platform-quick-actions">
        <button className="platform-destination ghost" onClick={() => onNavigate("pipelines")}>
          <strong>등록된 분석 Pipeline 사용하기 <span aria-hidden="true">↗</span></strong>
          <span>전문가가 구성한 분석 방법을 내 Dataset에 적용합니다.</span>
        </button>
        <button className="platform-destination ghost" onClick={() => onNavigate("understanding")}>
          <strong>데이터 이해하기 <span aria-hidden="true">↗</span></strong>
          <span>기존 Dataset에서 구조·분포·품질을 탐색합니다.</span>
        </button>
        <button className="platform-destination ghost" onClick={() => onNavigate("preparation")}>
          <strong>데이터 준비하기 <span aria-hidden="true">↗</span></strong>
          <span>탐색을 거치지 않고도 변환을 시작할 수 있습니다.</span>
        </button>
        <button className="platform-destination ghost" onClick={() => onNavigate("ml")}>
          <strong>모델과 결과 <span aria-hidden="true">↗</span></strong>
          <span>모델 판본을 발견하고 학습·평가 활동에 접근합니다.</span>
        </button>
        <button className="platform-destination ghost" onClick={() => onNavigate("scoring")}>
          <strong>기존 모델 사용하기 <span aria-hidden="true">↗</span></strong>
          <span>새 학습 없이 모델의 Scoring을 요청합니다.</span>
        </button>
      </div>
    </section>
    <section className="platform-outlook" aria-labelledby="future-experience-heading">
      <h3 id="future-experience-heading">확장할 경험</h3>
      <p>연구·실험과 모델 활용은 독립적인 제품 경험입니다. 목표 화면은 고려하되,
        현재 구현되지 않은 Experiment 관리와 온라인 Serving은 동작하는 기능으로 표시하지 않습니다.</p>
      <div className="platform-outlook-actions">
        <button className="secondary" onClick={() => onNavigate("research")}>연구 경험 살펴보기 →</button>
        <button className="secondary" onClick={() => onNavigate("experience")}>모델 활용 경험 살펴보기 →</button>
      </div>
    </section>
  </div>;
}
