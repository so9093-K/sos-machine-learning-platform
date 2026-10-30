import { algorithmLabel } from "./display-format";
import type { ModelRevision } from "./types";

function recordedNumber(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR") : "기록 없음";
}

/** 모델 판본에 고정된 생성 근거를 설명하며 새 학습·설명 계산은 제출하지 않는다. */
export function ModelCreationEvidence({ model, datasetName }: { model: ModelRevision; datasetName: string }) {
  const scoreCount = model.training_score_summary.score_count;
  return <section className="model-creation-evidence" aria-label="모델 생성 근거">
    <h3>모델 생성 과정</h3>
    <ol>
      <li><strong>학습 입력</strong> · {datasetName} · Feature {model.features.length}개</li>
      <li><strong>학습 방법</strong> · {algorithmLabel(model.algorithm)} · 학습 점수 계산 {typeof scoreCount === "number" && Number.isFinite(scoreCount) ? `${recordedNumber(scoreCount)}행` : "행 수 기록 없음"}</li>
      <li><strong>생성 결과</strong> · 고정된 모델 판본 · {model.registry_version ? `Registry v${model.registry_version}` : "Registry 판본 기록 없음"}</li>
    </ol>
    <p className="hint">학습 데이터의 점수는 독립 평가 성능이나 사건 발생 확률이 아니며, 이 기록만으로 다른 데이터에서의 정확도를 확정할 수 없습니다.</p>
  </section>;
}
