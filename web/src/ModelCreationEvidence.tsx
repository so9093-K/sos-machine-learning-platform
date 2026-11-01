import { algorithmLabel } from "./display-format";
import type { ModelRevision } from "./types";

function recordedRows(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("ko-KR")}행` : "기록 없음";
}

type ProducedEvaluation = { alert_fraction?: number; calibration_threshold?: number;
  test?: { alert_rate?: number; predicted_anomalies?: number; score_count?: number } };
/** MLflow에서 연결한 판본의 출처. 생산 Pipeline이 기록한 학습 근거를 SOS가 그대로 보관한다. */
export type ModelImportSource = {
  mlflow_model: string; mlflow_version: string; run_id?: string | null;
  training?: { producer?: string; data_reference?: string | null;
    rows?: { train?: number; calibration?: number; test?: number }; evaluation?: ProducedEvaluation };
};

/** 외부 Pipeline이 생산해 MLflow에서 연결한 판본이면 그 출처, SOS가 학습한 판본이면 null이다. */
export function modelImportSource(model: ModelRevision): ModelImportSource | null {
  return "source_kind" in model && model.source_kind === "mlflow_registry" && model.source
    ? model.source as ModelImportSource : null;
}

function rate(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 3 })}%` : "기록 없음";
}

/** 생산 Pipeline이 기록한 학습·평가 근거. SOS는 이 학습을 다시 실행하지 않았다. */
function ProducedTrainingInput({ model, source }: { model: ModelRevision; source: ModelImportSource }) {
  const rows = source.training?.rows;
  const evaluation = source.training?.evaluation;
  return <div className="model-training-input">
    <dl className="execution-facts">
      <div><dt>생산 Pipeline</dt><dd>{source.training?.producer ?? "기록 없음"}</dd></div>
      <div><dt>학습 입력</dt><dd>{recordedRows(rows?.train)} × {model.features.length} Feature</dd></div>
      {rows?.calibration != null && <div><dt>Calibration 구간</dt><dd>{recordedRows(rows.calibration)}</dd></div>}
      {rows?.test != null && <div><dt>Test 구간</dt><dd>{recordedRows(rows.test)}</dd></div>}
      {evaluation?.test && <div><dt>Test 판정 비율</dt><dd>{rate(evaluation.test.alert_rate)}
        {typeof evaluation.alert_fraction === "number" && ` · 기준 ${rate(evaluation.alert_fraction)}`}</dd></div>}
      <div><dt>결측 처리</dt><dd>Train 중앙값 대체</dd></div>
    </dl>
    {source.training?.data_reference && <p className="hint">학습 데이터 · {source.training.data_reference}</p>}
  </div>;
}

/** 불변 학습 기록에서 입력 규모를 설명한다. 참고 분포 표본 수를 학습 행 수로 쓰지 않는다. */
export function ModelTrainingInput({ model }: { model: ModelRevision }) {
  if (model.creation_evidence_visible === false) return <p className="hint">학습 규모·선택·결측 정책은 공급자·관리자 전용입니다.</p>;
  const imported = modelImportSource(model);
  if (imported) return <ProducedTrainingInput model={model} source={imported} />;
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
  const imported = modelImportSource(model);
  return <section className="model-creation-evidence" aria-label="모델 생성 근거">
    <h3>모델 생성 과정</h3>
    <p>{imported
      ? `MLflow ${imported.mlflow_model} v${imported.mlflow_version}에서 연결 · 외부 Pipeline 생산`
      : datasetName} · {algorithmLabel(model.algorithm)}</p>
    <ModelTrainingInput model={model} />
    {model.creation_evidence_visible !== false && <p>학습 점수 계산 {recordedRows(model.training_score_summary.score_count)}</p>}
    <p>{model.registry_version ? `Registry v${model.registry_version}` : "Registry 등록 미확인"}</p>
    <p className="hint">학습 데이터의 점수는 독립 평가 성능이나 사건 발생 확률이 아니며, 이 기록만으로 다른 데이터에서의 정확도를 확정할 수 없습니다.</p>
  </section>;
}
