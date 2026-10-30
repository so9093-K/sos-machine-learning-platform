/** 학습 유형 → 분석 과제 → 주제로 이어지는 ML 기능 소개 화면.
 *
 *  어떤 분류·주제가 실제로 제공되는지와 주제의 입력·출력 계약은 분석 주제 카탈로그(/api/topics)가
 *  정본이고, 사용자에게 보여 줄 가치·활용 설명은 이 파일이 소유한다. 주제는 등록 Pipeline에 묶이지 않는다.
 */
import { useEffect, useState, type ReactNode } from "react";
import { formatError, request } from "./api";
import { isTopicCompatible, modelLabTree, type ModelLabTree } from "./ModelLabTaxonomy";
import type { AnalysisTopic, ModelRevision } from "./types";

type Point = readonly [number, number];
type GuideItem = { title: string; text: string };
type Guide = {
  title: string;
  summary: string;
  uses: GuideItem[];
  reading?: GuideItem[];
};

export const learningTypeGuides: Record<string, Guide> = {
  unsupervised: {
    title: "비지도 학습",
    summary: "정답이 표시되지 않은 데이터에서 반복되는 구조와 그 구조를 벗어난 관측을 찾아냅니다.",
    uses: [
      { title: "정답을 모으기 어려운 데이터", text: "사고·고장처럼 드물거나 판단에 시간이 드는 사건은 정답을 충분히 모으기 어렵습니다. 데이터의 평소 모습만으로 시작할 수 있습니다." },
      { title: "규칙에 없던 변화", text: "미리 정한 조건이 없어도 평소 패턴과 비교해 새로운 형태의 변화를 드러냅니다." },
      { title: "검토 대상 선별", text: "사람이 모든 데이터를 볼 수 없을 때 먼저 확인할 관측을 고릅니다." },
    ],
  },
};

export const taskGuides: Record<string, Guide> = {
  anomaly_detection: {
    title: "이상 탐지",
    summary: "평소 데이터의 패턴을 학습하고, 그 패턴에서 벗어난 관측일수록 높은 점수를 줍니다.",
    uses: [
      { title: "검토 우선순위", text: "점수가 높은 관측부터 확인해 한정된 검토 시간을 집중합니다." },
      { title: "변화 모니터링", text: "같은 모델로 주기적으로 점수를 계산해 높은 점수가 늘어나는지 지켜봅니다." },
      { title: "판정 기준 운영", text: "점수 분포를 보고 검토 대상으로 삼을 기준을 정해 같은 기준으로 반복 적용합니다." },
    ],
    reading: [
      { title: "상대적 순위", text: "같은 모델의 점수끼리 비교합니다. 사고가 일어날 확률이나 문제의 확정이 아닙니다." },
      { title: "높은 점수의 원인 확인", text: "높은 점수의 관측에서 어떤 값이 학습 기준과 달랐는지 비교하고 기여도를 계산해 봅니다." },
      { title: "새로운 정상 변화", text: "학습 데이터에 없던 정상적인 변화도 평소와 다르면 높은 점수를 받습니다." },
    ],
  },
};

export const topicGuides: Record<string, Guide & { route: "topic" }> = {
  activity_anomaly: {
    route: "topic",
    title: "활동 이상 탐지",
    summary: "사용자·호스트의 활동 집계에서 평소와 다른 패턴을 찾아 먼저 살펴볼 대상을 좁힙니다.",
    uses: [
      { title: "검토 대상 선별", text: "많은 사용자·호스트 중 평소와 가장 다르게 활동한 관측부터 확인합니다." },
      { title: "평소와의 차이 확인", text: "파일·네트워크·프로세스 활동 중 어떤 값이 학습 기준과 달랐는지 관측별로 비교합니다." },
      { title: "운영 연동", text: "같은 입력을 API로 보내 정기 점검이나 알림 우선순위에 점수를 활용합니다." },
    ],
  },
};

/** 화면에서 직접 체험할 수 있는 주제. 카탈로그에만 있고 Playground가 없는 주제는 안내하지 않는다. */
function hasPlayground(topic: Pick<AnalysisTopic, "id">): boolean {
  return topicGuides[topic.id]?.route === "topic";
}

/** 모델이 입력·출력 계약이 맞는 체험 주제를 가지면 그 주제를 돌려준다. 알고리즘은 묻지 않는다. */
export function playgroundTopicFor(model: Pick<ModelRevision, "features" | "output_type">,
  topics: readonly AnalysisTopic[] | null): string | null {
  return topics?.find(topic => hasPlayground(topic) && isTopicCompatible(model, topic))?.id ?? null;
}

/** 분석 주제 카탈로그. 제공 분류와 주제의 입력·출력 계약을 함께 확인한다. */
export function useTopicCatalog() {
  const [items, setItems] = useState<AnalysisTopic[] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    void request<AnalysisTopic[]>("/api/topics")
      .then(value => { if (active) setItems(value); })
      .catch(cause => { if (active) setError(formatError(cause)); });
    return () => { active = false; };
  }, [retry]);
  const playable = (items ?? []).filter(hasPlayground).map(topic => ({ ...topic, topic: topic.id }));
  return { items, tree: modelLabTree(playable), error, reload: () => setRetry(value => value + 1) };
}

export type TaxonomyLinks = {
  onOpenLearningType: (id: string) => void;
  onOpenTask: (id: string) => void;
  onOpenTopic: (topic: string) => void;
};

/** 분류 경로. 상위 단계로 바로 돌아갈 수 있게 한다. */
export function TaxonomyTrail({ learningType, task, topic, links }: {
  learningType?: string; task?: string; topic?: string; links: TaxonomyLinks;
}) {
  const steps: Array<{ key: string; label: string; open?: () => void }> = [];
  if (learningType) steps.push({ key: "learning", label: learningTypeGuides[learningType]?.title ?? learningType,
    open: () => links.onOpenLearningType(learningType) });
  if (task) steps.push({ key: "task", label: taskGuides[task]?.title ?? task, open: () => links.onOpenTask(task) });
  if (topic) steps.push({ key: "topic", label: topicGuides[topic]?.title ?? topic });
  return <nav className="taxonomy-trail" aria-label="분류 경로">
    <ol>{steps.map((step, index) => <li key={step.key}>
      {index < steps.length - 1 && step.open
        ? <button type="button" className="ghost" onClick={step.open}>{step.label}</button>
        : <span aria-current="page">{step.label}</span>}
    </li>)}</ol>
  </nav>;
}

function GuideCards({ heading, items }: { heading: string; items: GuideItem[] }) {
  return <section className="guide-section" aria-label={heading}>
    <h2>{heading}</h2>
    <div className="guide-cards">{items.map(item => <article key={item.title} className="guide-card">
      <h3>{item.title}</h3><p>{item.text}</p>
    </article>)}</div>
  </section>;
}

function CatalogState({ error, loading, onRetry }: { error: string; loading: boolean; onRetry: () => void }) {
  if (error) return <div role="alert" className="guide-state"><p>제공 중인 분석 목록을 불러오지 못했습니다. {error}</p>
    <button type="button" className="secondary" onClick={onRetry}>다시 조회</button></div>;
  if (loading) return <p role="status" className="muted">제공 중인 분석을 확인하는 중입니다.</p>;
  return null;
}

/** 학습 유형 소개. 개요·도식과 SOS에서 체험할 수 있는 분석 과제를 보여준다. */
export function LearningTypePage({ id, links }: { id: string; links: TaxonomyLinks }) {
  const catalog = useTopicCatalog();
  const guide = learningTypeGuides[id];
  const tasks = catalog.tree.find(item => item.id === id)?.tasks ?? [];
  if (!guide) return <section className="panel"><h2>알 수 없는 학습 유형</h2>
    <p className="muted">요청한 학습 유형을 찾을 수 없습니다. 왼쪽 메뉴에서 다시 선택하세요.</p></section>;
  return <div className="guide-page">
    <section className="panel guide-hero">
      <p className="guide-lead">{guide.summary}</p>
      {id === "unsupervised" && <UnsupervisedDiagram />}
    </section>
    <GuideCards heading="활용 상황" items={guide.uses} />
    <section className="guide-section" aria-labelledby="learning-tasks">
      <h2 id="learning-tasks">체험 가능한 분석 과제</h2>
      <CatalogState error={catalog.error} loading={catalog.items === null} onRetry={catalog.reload} />
      {catalog.items && tasks.length === 0 && <p className="muted">아직 체험할 수 있는 분석 과제가 없습니다.</p>}
      <div className="guide-choices">{tasks.map(task => <button key={task.id} type="button"
        className="surface-button guide-choice" onClick={() => links.onOpenTask(task.id)}>
        <span className="eyebrow">분석 과제</span>
        <strong>{taskGuides[task.id]?.title ?? task.label}</strong>
        <span>{taskGuides[task.id]?.summary}</span>
        <small>{task.topics.map(topic => topicGuides[topic.topic]?.title ?? topic.name).join(" · ")} →</small>
      </button>)}</div>
    </section>
  </div>;
}

/** 분석 과제 소개. 원리 도식·활용·결과 읽는 법과 체험할 주제로 연결한다. */
export function AnalysisTaskPage({ id, links }: { id: string; links: TaxonomyLinks }) {
  const catalog = useTopicCatalog();
  const guide = taskGuides[id];
  const learning = catalog.tree.find(item => item.tasks.some(task => task.id === id));
  const topics = learning?.tasks.find(task => task.id === id)?.topics ?? [];
  if (!guide) return <section className="panel"><h2>알 수 없는 분석 과제</h2>
    <p className="muted">요청한 분석 과제를 찾을 수 없습니다. 왼쪽 메뉴에서 다시 선택하세요.</p></section>;
  return <div className="guide-page">
    {learning && <TaxonomyTrail learningType={learning.id} task={id} links={links} />}
    <section className="panel guide-hero">
      <p className="guide-lead">{guide.summary}</p>
      {id === "anomaly_detection" && <AnomalyDiagram />}
    </section>
    <GuideCards heading="활용 방법" items={guide.uses} />
    {guide.reading && <GuideCards heading="결과 해석" items={guide.reading} />}
    <section className="guide-section" aria-labelledby="task-topics">
      <h2 id="task-topics">체험 가능한 주제</h2>
      <CatalogState error={catalog.error} loading={catalog.items === null} onRetry={catalog.reload} />
      {catalog.items && topics.length === 0 && <p className="muted">아직 체험할 수 있는 주제가 없습니다.</p>}
      <div className="guide-choices">{topics.map(topic => <button key={topic.id} type="button"
        className="surface-button guide-choice" onClick={() => links.onOpenTopic(topic.topic)}>
        <span className="eyebrow">주제 · Playground</span>
        <strong>{topicGuides[topic.topic]?.title ?? topic.name}</strong>
        <span>{topicGuides[topic.topic]?.summary}</span>
        <small>Playground →</small>
      </button>)}</div>
    </section>
  </div>;
}

export function topicTree(tree: ModelLabTree, topic: string) {
  for (const learning of tree) for (const task of learning.tasks) {
    if (task.topics.some(item => item.topic === topic)) return { learningType: learning.id, task: task.id };
  }
  return null;
}

// 도식의 점은 개념 예시다. 실행할 때마다 모양이 바뀌지 않도록 고정 좌표를 쓴다.
const groupA: Point[] = [[70, 98], [86, 108], [96, 90], [80, 122], [102, 114], [112, 100], [92, 130], [74, 112], [104, 128]];
const groupB: Point[] = [[150, 168], [166, 178], [158, 156], [176, 162], [168, 192], [186, 180], [146, 184], [180, 146]];
const strays: Point[] = [[196, 72], [52, 194]];

function Dots({ points, dx = 0, className }: { points: Point[]; dx?: number; className: string }) {
  return <>{points.map(([x, y]) => <circle key={`${x}-${y}`} cx={x + dx} cy={y} r={5} className={className} />)}</>;
}

function Arrow({ x, y }: { x: number; y: number }) {
  return <path d={`M${x} ${y}h26m-7 -6l7 6l-7 6`} className="diagram-arrow" />;
}

function DiagramFrame({ label, children }: { label: string; children: ReactNode }) {
  return <figure className="guide-diagram">
    <svg viewBox="0 0 760 250" role="img" aria-label={label}>{children}</svg>
  </figure>;
}

/** 정답 없는 데이터 → 평소 구조 학습 → 찾아낸 것. */
export function UnsupervisedDiagram() {
  return <DiagramFrame label="정답 없는 데이터에서 평소 구조를 학습해 묶음, 벗어난 관측, 핵심 요약을 찾는 비지도 학습의 개념도">
    <text x={20} y={26} className="diagram-title">1. 정답이 없는 데이터</text>
    <rect x={20} y={40} width={210} height={190} rx={10} className="diagram-box" />
    <Dots points={[...groupA, ...groupB, ...strays]} className="diagram-dot" />
    <Arrow x={240} y={135} />
    <text x={280} y={26} className="diagram-title">2. 평소 구조 학습</text>
    <rect x={280} y={40} width={210} height={190} rx={10} className="diagram-box" />
    <ellipse cx={351} cy={111} rx={40} ry={32} className="diagram-group" />
    <ellipse cx={426} cy={170} rx={38} ry={34} className="diagram-group" />
    <Dots points={[...groupA, ...groupB]} dx={260} className="diagram-dot-known" />
    {strays.map(([x, y]) => <g key={x}><circle cx={x + 260} cy={y} r={11} className="diagram-ring" />
      <circle cx={x + 260} cy={y} r={5} className="diagram-dot-odd" /></g>)}
    <Arrow x={500} y={135} />
    <text x={540} y={26} className="diagram-title">3. 발견 결과</text>
    <g transform="translate(540 52)">
      <rect width={200} height={52} rx={9} className="diagram-box" />
      <circle cx={22} cy={22} r={4} className="diagram-dot-known" /><circle cx={30} cy={30} r={4} className="diagram-dot-known" />
      <circle cx={18} cy={33} r={4} className="diagram-dot-known" />
      <text x={46} y={23} className="diagram-label">유사한 관측의 묶음</text>
      <text x={46} y={41} className="diagram-note">군집화</text>
    </g>
    <g transform="translate(540 114)">
      <rect width={200} height={52} rx={9} className="diagram-box diagram-box-active" />
      <circle cx={24} cy={26} r={10} className="diagram-ring" /><circle cx={24} cy={26} r={5} className="diagram-dot-odd" />
      <text x={46} y={23} className="diagram-label">평소와 다른 관측</text>
      <text x={46} y={41} className="diagram-note diagram-note-active">이상 탐지 · SOS 체험 가능</text>
    </g>
    <g transform="translate(540 176)">
      <rect width={200} height={52} rx={9} className="diagram-box" />
      <path d="M14 38L34 16M14 26L28 34" className="diagram-axis" />
      <text x={46} y={23} className="diagram-label">핵심 특성 요약</text>
      <text x={46} y={41} className="diagram-note">차원 축소</text>
    </g>
  </DiagramFrame>;
}

const normal: Point[] = Array.from({ length: 34 }, (_, index) => {
  const angle = index * 2.399963;
  const radius = 9 * Math.sqrt(index + 1);
  return [175 + Math.cos(angle) * radius * 1.35, 140 + Math.sin(angle) * radius * 0.85] as const;
});
const outliers: Point[] = [[62, 62], [320, 74], [304, 212]];

/** 평소 패턴에서 벗어난 관측일수록 높은 점수 → 점수순으로 먼저 살펴볼 대상. */
export function AnomalyDiagram() {
  const ranks = [
    { label: "관측 A", width: 230, odd: true }, { label: "관측 B", width: 205, odd: true },
    { label: "관측 C", width: 176, odd: true }, { label: "평소 관측", width: 74, odd: false },
    { label: "평소 관측", width: 66, odd: false }, { label: "평소 관측", width: 61, odd: false },
  ];
  return <DiagramFrame label="평소 패턴에서 벗어난 관측일수록 이상 점수가 높아 점수순 상위부터 살펴보는 이상 탐지의 개념도">
    <text x={20} y={26} className="diagram-title">평소 패턴과 벗어난 관측</text>
    <rect x={20} y={40} width={340} height={190} rx={10} className="diagram-box" />
    <ellipse cx={175} cy={140} rx={78} ry={50} className="diagram-group" />
    <Dots points={normal} className="diagram-dot-known" />
    {outliers.map(([x, y]) => <g key={x}><circle cx={x} cy={y} r={11} className="diagram-ring" />
      <circle cx={x} cy={y} r={5} className="diagram-dot-odd" /></g>)}
    <text x={175} y={222} textAnchor="middle" className="diagram-note">평소 패턴</text>
    <Arrow x={372} y={135} />
    <text x={412} y={26} className="diagram-title">이상 점수 순위</text>
    {ranks.map((rank, index) => <g key={index} transform={`translate(412 ${46 + index * 30})`}>
      <text y={15} className="diagram-label">{rank.label}</text>
      <rect x={70} y={4} width={rank.width} height={14} rx={4}
        className={rank.odd ? "diagram-bar-odd" : "diagram-bar"} />
    </g>)}
    <path d="M722 50v80" className="diagram-brace" />
    <text x={740} y={94} className="diagram-note diagram-note-active" transform="rotate(90 740 94)" textAnchor="middle">우선 검토 대상</text>
  </DiagramFrame>;
}
