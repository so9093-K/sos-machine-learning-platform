/** 분석 주제 카탈로그의 독립 분류 차원을 사용자에게 읽을 수 있게 표시한다. */
import type { AnalysisTopic, ModelRevision } from "./types";

/** 모델이 주제의 입력·출력 계약을 따른다. 알고리즘과 등록 Pipeline은 보지 않는다.
 *  입력은 Feature 이름으로 대응하므로 순서는 비교하지 않는다. */
export function isTopicCompatible(model: Pick<ModelRevision, "features" | "output_type">,
  topic: Pick<AnalysisTopic, "required_features" | "output_type">): boolean {
  const required = new Set(topic.required_features);
  return model.output_type === topic.output_type && model.features.length === required.size &&
    model.features.every(feature => required.has(feature));
}

const learningTypes: Record<string, string> = { unsupervised: "비지도 학습" };
const taskTypes: Record<string, string> = { anomaly_detection: "이상 탐지" };
const topics: Record<string, string> = { activity_anomaly: "활동 이상 탐지" };

export type ModelLabClassification = {
  learning_type: string; task_type: string; topic: string;
};

export function modelLabLabels(info: ModelLabClassification): [string, string, string] {
  return [
    learningTypes[info.learning_type] ?? info.learning_type,
    taskTypes[info.task_type] ?? info.task_type,
    topics[info.topic] ?? info.topic,
  ];
}

/** 표시용 트리는 등록된 독립 분류 속성의 뷰일 뿐 영속적인 부모·자식 계약이 아니다. */
export type ModelLabTopic = ModelLabClassification & { id: string; name: string };
export type ModelLabTree = Array<{
  id: string; label: string;
  tasks: Array<{ id: string; label: string; topics: ModelLabTopic[] }>;
}>;

export function modelLabTree(definitions: readonly ModelLabTopic[]): ModelLabTree {
  const learning = new Map<string, Map<string, ModelLabTopic[]>>();
  for (const item of definitions) {
    const tasks = learning.get(item.learning_type) ?? new Map<string, ModelLabTopic[]>();
    const topicsForTask = tasks.get(item.task_type) ?? [];
    if (!topicsForTask.some(previous => previous.id === item.id)) topicsForTask.push(item);
    tasks.set(item.task_type, topicsForTask);
    learning.set(item.learning_type, tasks);
  }
  const byLabel = (a: { label: string }, b: { label: string }) =>
    a.label.localeCompare(b.label, "ko");
  return [...learning.entries()].map(([id, tasks]) => ({
    id,
    label: learningTypes[id] ?? id,
    tasks: [...tasks.entries()].map(([taskId, topicsForTask]) => ({
      id: taskId,
      label: taskTypes[taskId] ?? taskId,
      topics: [...topicsForTask].sort((a, b) =>
        modelLabLabels(a)[2].localeCompare(modelLabLabels(b)[2], "ko")),
    })).sort(byLabel),
  })).sort(byLabel);
}
