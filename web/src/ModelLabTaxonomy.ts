/** Pipeline Catalog의 독립 분류 차원을 사용자에게 읽을 수 있게 표시한다. */
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
