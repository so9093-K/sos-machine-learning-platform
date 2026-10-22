import { useEffect, useState } from "react";
import { formatError } from "./api";
import { usePagedJobs } from "./usePagedJobs";
import type { MLTrainingJobSummary } from "./types";
import type { WorkspaceTab } from "./PlatformNavigation";

type Scope = "research" | "experience";
const content = {
  research: {
    heading: "Research & Experiments",
    description: "실험을 비교하고, 근거를 이해하고, 기존 실행을 새로운 연구로 발전시키는 경험입니다.",
    goals: [
      ["조건 비교", "데이터·Feature·실행 설정을 함께 살펴봅니다."],
      ["재현과 분기", "기존 Job의 조건을 바탕으로 새로운 Job을 생성합니다."],
      ["연구의 연속성", "실패와 성공의 실행 근거를 모두 보존해 다음 연구에 활용합니다."],
    ],
  },
  experience: {
    heading: "Model Experience",
    description: "모델을 새로 학습하는 사용자와 별개로, 기존 모델의 입력·출력과 제약을 이해하고 활용하는 경험입니다.",
    goals: [
      ["모델 이해", "입력 Feature, 판본, 모델이 계산한 점수의 의미를 확인합니다."],
      ["직접 활용", "사용할 수 있는 모델 판본으로 데이터의 점수를 계산합니다."],
      ["서비스 확장", "추론·연동이 지원되는 경우 실제 이용 방법과 상태를 확인합니다."],
    ],
  },
} as const;

/** 장기 Research·Serving 경험을 설계하면서, 현재 지원되는 모델 판본과 실행 진입은 제공한다. */
export function PlatformAreaPreview({ area, onNavigate, onOpenModel, onStartScoring, canRun }: {
  area: Scope;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenModel: (id: string) => void;
  onStartScoring: (id: string) => void;
  canRun: boolean;
}) {
  const entry = content[area];
  const history = usePagedJobs<MLTrainingJobSummary>("/api/ml/train/jobs");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true);
    void history.refresh()
      .catch(e => { if (active) setError(formatError(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const revisions = history.items.filter(item =>
    item.status === "Succeeded" && typeof item.result?.model_revision_id === "string").slice(0, 5);
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">{area === "research" ? "RESEARCH PLATFORM" : "MODEL EXPERIENCE"}</span>
      <h2>{entry.heading}</h2>
      <p>{entry.description}</p>
    </section>
    <section className="panel" aria-labelledby="available-models-heading">
      <div className="panel-head"><div>
        <h3 id="available-models-heading">
          {area === "research" ? "연결된 학습 결과" : "확인할 수 있는 모델 판본"}
        </h3>
        <p>현재 조회된 학습 Job에서 발견한 모델 결과입니다. 전체 Model Registry는 아닙니다.</p>
      </div><button className="secondary" onClick={() => onNavigate("ml")}>모델·실행 전체 보기 →</button></div>
      {loading && <p role="status">모델 판본의 근거를 조회하고 있습니다.</p>}
      {error && <p role="alert" className="hint">{error}</p>}
      {!loading && revisions.length === 0 && <p className="muted">
        {error ? "학습 이력을 조회하지 못했습니다." : "현재 조회 범위에 모델 판본이 없습니다."}
      </p>}
      <div className="platform-compact-list">
        {revisions.map(item => <div className="platform-discovered-model" key={item.id}>
          <div>
            <strong>Model Revision {String(item.result?.model_revision_id).slice(0, 12)}</strong>
            <p className="muted">학습 Job {item.id.slice(0, 12)} · {new Date(item.created_at).toLocaleString("ko-KR")}</p>
          </div>
          <div className="platform-outlook-actions">
            <button className="secondary" onClick={() => onOpenModel(String(item.result?.model_revision_id))}>
              판본·입력 근거 확인 →
            </button>
            {area === "experience" && canRun &&
              <button onClick={() => onStartScoring(String(item.result?.model_revision_id))}>
                배치 점수화 시작 →
              </button>}
          </div>
        </div>)}
      </div>
    </section>
    <section aria-labelledby="experience-goals-heading">
      <h3 id="experience-goals-heading" className="platform-section-title">이 영역의 목표 경험</h3>
      <div className="platform-destinations">
        {entry.goals.map(([title, description]) => <article key={title} className="platform-concept-card">
          <h4>{title}</h4><p>{description}</p>
        </article>)}
      </div>
    </section>
    <section className="platform-outlook" aria-label="현재 제공 범위">
      <h3>현재 지원되는 범위</h3>
      <p>{area === "research"
        ? "기존 학습 결과 조회와 Job별 비교·Fork·Reproduce는 Models에서 사용할 수 있습니다. 독립 Experiment 등록·관리 경험은 아직 구현되지 않았습니다."
        : "모델 판본의 입력·결과 근거를 확인하고 배치 Scoring을 실행할 수 있습니다. 온라인 Serving·Playground·외부 배포는 아직 구현되지 않았습니다."}
      </p>
    </section>
  </div>;
}
