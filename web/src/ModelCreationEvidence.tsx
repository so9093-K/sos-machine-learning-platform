import { algorithmLabel } from "./display-format";
import type { ModelRevision } from "./types";

function recordedRows(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("ko-KR")}행` : "기록 없음";
}

/** 불변 학습 기록에서 입력 규모를 설명한다. 참고 분포 표본 수를 학습 행 수로 쓰지 않는다. */
export function ModelTrainingInput({ model }: { model: ModelRevision }) {
  const selection = model.runtime.training_selection as Record<string, unknown> | undefined;
  const split = selection?.temporal_split as Record<string, unknown> | undefined;
  const profile = model.runtime.feature_training_profile as Record<string, unknown> | undefined;
  const method = selection?.method === "all_rows" ? "대상 전체 행" : selection?.requested_rows != null ? "명시적 표본" : "기록 없음";
  return <div className="model-training-input">
    <dl className="execution-facts">
      {split && <div><dt>시간 분할 전 입력</dt><dd>{recordedRows(split.input_total_rows)}</dd></div>}
      <div><dt>{split ? "Train 구간 선택 대상" : "학습 선택 대상"}</dt><dd>{recordedRows(selection?.source_rows)}</dd></div>
      <div><dt>실제 학습 입력</dt><dd>{recordedRows(selection?.selected_rows)} × {model.features.length} Feature</dd></div>
      <div><dt>학습 입력 선택</dt><dd>{method}</dd></div>
      <div><dt>결측 처리</dt><dd>{model.runtime.training_missing_policy === "train_median" ? "Train 중앙값 대체" : model.runtime.training_missing_policy === "reject" ? "결측 입력 거부" : "기록 없음"}</dd></div>
    </dl>
    {profile && <p className="hint">참고 분포 분석 {recordedRows(profile.sample_rows ?? profile.row_count)} · 모델 학습 행 수와 다른 집계입니다.</p>}
  </div>;
}

/** 모델 판본의 고정된 생성 근거. 새 학습·설명 계산을 제출하지 않는다. */
export function ModelCreationEvidence({ model, datasetName }: { model: ModelRevision; datasetName: string }) {
  return <section className="model-creation-evidence" aria-label="모델 생성 근거">
    <h3>모델 생성 과정</h3>
    <p>{datasetName} · {algorithmLabel(model.algorithm)}</p>
    <ModelTrainingInput model={model} />
    <p>학습 점수 계산 {recordedRows(model.training_score_summary.score_count)} · {model.registry_version ? `Registry v${model.registry_version}` : "Registry 등록 미확인"}</p>
    <p className="hint">학습 데이터의 점수는 독립 평가 성능이나 사건 발생 확률이 아니며, 이 기록만으로 다른 데이터에서의 정확도를 확정할 수 없습니다.</p>
  </section>;
}
