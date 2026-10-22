import type { WorkspaceTab } from "./PlatformNavigation";

type DatasetLink = { id: string; name: string; kind: string };
type Activity = { id: string; status: string; created_at: string };
type Props = {
  datasets: DatasetLink[];
  jobs: Activity[];
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenDataset: (datasetId: string) => void;
};

const destinations: Array<{ tab: WorkspaceTab; heading: string; summary: string }> = [
  { tab: "understanding", heading: "데이터 이해하기", summary: "Dataset의 구조·분포·품질을 확인하고 탐색합니다." },
  { tab: "preparation", heading: "데이터 준비하기", summary: "원본을 보존하면서 변환하고 새 결과를 만듭니다." },
  { tab: "ml", heading: "모델 살펴보기", summary: "모델 판본과 결과를 확인하고, 필요할 때 새 학습을 시작합니다." },
  { tab: "jobs", heading: "실행 확인하기", summary: "데이터 작업의 실행 상태와 결과를 확인합니다." },
];

/** Overview는 실행 단계가 아니라 독립 기능으로 향하는 출발점이다. */
export function PlatformOverview({ datasets, jobs, onNavigate, onOpenDataset }: Props) {
  const recent = [...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0,3);
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">EXPLORE · EXPERIMENT · UNDERSTAND · EVOLVE</span>
      <h2>무엇을 살펴보고 싶으신가요?</h2>
      <p>데이터 탐색과 모델 실험, 실행 결과 확인은 독립적으로 시작할 수 있습니다.
        특정 파이프라인을 거칠 필요가 없습니다.</p>
    </section>
    <section aria-labelledby="entry-heading">
      <h3 id="entry-heading" className="platform-section-title">작업 시작</h3>
      <div className="platform-destinations">
        {destinations.map(item => <button key={item.tab} type="button"
          className="platform-destination ghost" onClick={() => onNavigate(item.tab)}>
          <strong>{item.heading} <span aria-hidden="true">↗</span></strong>
          <span>{item.summary}</span>
        </button>)}
      </div>
    </section>
    <div className="platform-overview-grid">
      <section className="panel" aria-labelledby="dataset-heading">
        <div className="panel-head"><div><h3 id="dataset-heading">Dataset</h3>
          <p>최근 불러온 목록에서 바로 데이터로 이동합니다.</p></div>
          <button className="secondary" onClick={() => onNavigate("understanding")}>모두 보기</button></div>
        {datasets.length === 0 ? <p className="muted">등록된 Dataset이 없습니다. 데이터 탐색에서 시작하세요.</p> :
          <div className="platform-compact-list">{datasets.slice(0,4).map(item =>
            <button key={item.id} className="platform-list-link ghost" onClick={() => onOpenDataset(item.id)}>
              <span className="platform-resource-name">{item.name}</span><small>{item.kind}</small>
            </button>)}</div>}
      </section>
      <section className="panel" aria-labelledby="activity-heading">
        <div className="panel-head"><div><h3 id="activity-heading">최근 데이터 작업</h3>
          <p>현재 조회된 데이터·탐색 실행 기록입니다.</p></div>
          <button className="secondary" onClick={() => onNavigate("jobs")}>이력 보기</button></div>
        {recent.length === 0 ? <p className="muted">조회된 데이터 작업이 없습니다.</p> :
          <div className="platform-compact-list">{recent.map(item =>
            <button key={item.id} className="platform-list-link ghost" onClick={() => onNavigate("jobs")}>
              <span className="platform-resource-name">Job {item.id.slice(0,8)}</span>
              <small>{item.status}</small>
            </button>)}</div>}
      </section>
    </div>
    <section className="platform-outlook" aria-labelledby="outlook-heading">
      <h3 id="outlook-heading">플랫폼의 다음 경험</h3>
      <p>연구·실험과 모델 활용 경험도 SOS의 독립적인 영역입니다. 현재 구현된 기능과
        구상 중인 경험을 구분해 살펴볼 수 있습니다.</p>
      <div className="platform-outlook-actions">
        <button className="secondary" onClick={() => onNavigate("research")}>연구·실험 화면 구상 →</button>
        <button className="secondary" onClick={() => onNavigate("experience")}>모델 활용 화면 구상 →</button>
      </div>
    </section>
  </div>;
}
