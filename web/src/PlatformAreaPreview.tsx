import type { WorkspaceTab } from "./PlatformNavigation";

type Scope = "research" | "experience";
const content = {
  research: {
    heading: "연구·실험 경험",
    description: "연구 목적에서 출발하여 실험을 살펴보고 비교·재현하며 결과를 이해하는 공간입니다.",
    goals: [
      ["실험 발견", "연구 목적과 연결된 모델·실행·평가 결과를 찾습니다."],
      ["조건 비교", "데이터 판본·설정·평가 근거를 함께 비교합니다."],
      ["재현과 발전", "기존 실행을 참고해 새로운 실험으로 발전시킵니다."],
    ],
  },
  experience: {
    heading: "모델 활용 경험",
    description: "모델을 개발하는 사용자와 별개로, 모델의 목적·입출력·제약을 이해하고 활용하는 공간입니다.",
    goals: [
      ["모델 발견", "제공되는 모델의 목적, 지원 입력, 판본을 확인합니다."],
      ["모델 체험", "허용된 입력으로 모델의 동작과 결과를 이해합니다."],
      ["외부 연동", "서비스 상태와 사용 가능한 추론·연동 방법을 확인합니다."],
    ],
  },
} as const;

/** 백엔드에 없는 실험 관리·Serving 기능을 동작하는 것처럼 표현하지 않는다. */
export function PlatformAreaPreview({ area, onNavigate }: {
  area: Scope;
  onNavigate: (tab: WorkspaceTab) => void;
}) {
  const entry = content[area];
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">EXPERIENCE CONCEPT · 기능 미구현</span>
      <h2>{entry.heading}</h2>
      <p>{entry.description}</p>
    </section>
    <section aria-labelledby="concept-heading">
      <h3 id="concept-heading" className="platform-section-title">이 영역에서 지원할 사용자 목표</h3>
      <div className="platform-destinations">
        {entry.goals.map(([title, description]) => <article key={title} className="platform-concept-card">
          <h4>{title}</h4><p>{description}</p>
        </article>)}
      </div>
    </section>
    <section className="panel">
      <h3>현재 사용 가능한 기능</h3>
      <p className="hint">이 화면은 장기 경험을 검토하기 위한 설계 시안입니다.
        Experiment 관리나 온라인 Serving을 실행하는 화면이 아닙니다.</p>
      <div className="platform-outlook-actions">
        <button onClick={() => onNavigate("ml")}>모델 학습·결과 보기 →</button>
        <button className="secondary" onClick={() => onNavigate("understanding")}>데이터 살펴보기 →</button>
      </div>
    </section>
  </div>;
}
