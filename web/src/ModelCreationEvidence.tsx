import { useState } from "react";
import { formatError, request } from "./api";
import { algorithmLabel, jobStatusLabel } from "./display-format";
import type { MLTrainingJob, ModelRevision } from "./types";

function recordedNumber(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR") : "기록 없음";
}

/** 모델 생성의 실제 기록을 설명하며 새 학습·설명 계산은 제출하지 않는다. */
export function ModelCreationEvidence({ model, datasetName }: { model: ModelRevision; datasetName: string }) {
  const [job, setJob] = useState<MLTrainingJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    if (loading) return;
    setLoading(true); setError("");
    try {
      const source = await request<MLTrainingJob>(`/api/ml/train/jobs/${encodeURIComponent(model.job_id)}`);
      if (source.id !== model.job_id || source.kind !== "ml_train" ||
          source.result?.model_revision_id !== model.id) {
        throw new Error("이 모델을 생성한 학습 기록과 일치하지 않습니다.");
      }
      setJob(source);
    } catch (cause) { setError(formatError(cause)); }
    finally { setLoading(false); }
  }
  const scoreCount = model.training_score_summary.score_count;
  const input = job?.spec?.training_input as { mode?: string; rows?: number; seed?: number } | undefined;
  return <section className="model-creation-evidence" aria-label="모델 생성 근거">
    <h3>모델 생성 과정</h3>
    <ol>
      <li><strong>학습 입력</strong> · {datasetName} · Feature {model.features.length}개</li>
      <li><strong>학습 방법</strong> · {algorithmLabel(model.algorithm)} · 학습 점수 계산 {typeof scoreCount === "number" && Number.isFinite(scoreCount) ? `${recordedNumber(scoreCount)}행` : "행 수 기록 없음"}</li>
      <li><strong>생성 결과</strong> · 고정된 모델 판본 · {model.registry_version ? `Registry v${model.registry_version}` : "Registry 판본 기록 없음"}</li>
    </ol>
    <p className="hint">저장된 실행 근거 · 추가 학습 없음</p>
    <h4>적용 범위·한계</h4>
    <p>입력 Feature 이름·순서·자료형을 확인한 뒤 데이터를 적용하세요. 학습 데이터의 점수는 독립 평가 성능이나 사건 발생 확률이 아닙니다. 이 기록만으로 다른 데이터에서의 정확도나 적합성을 확정할 수 없습니다.</p>
    <details onToggle={event => { if (event.currentTarget.open && !job && !error) void load(); }}>
      <summary>기록된 학습 범위·설정</summary>
      {loading && <p role="status">학습 기록 조회 중…</p>}
      {error && <p role="alert">{error}</p>}
      {job && <>
        <p>학습 Job · {jobStatusLabel(job.status)} · 연구 묶음 {String(job.spec?.experiment_title ?? "기록 없음")}</p>
        <p>입력 범위 · {input?.mode === "all" ? "전체 Dataset" : input?.mode === "sample"
          ? `부분집합 요청 ${recordedNumber(input.rows)}행 · Seed ${recordedNumber(input.seed)}` : "기록 없음"}</p>
        {job.spec?.temporal_split != null && <p className="hint">시간 분할 학습입니다. 전체 Dataset 행 수와 실제 Train 학습 행 수는 다릅니다.</p>}
        <p>결측 처리 · {job.spec?.missing_policy === "reject" ? "결측 거부" : job.spec?.missing_policy === "train_median" ? "Train 중앙값 대체" : "기록 없음"}</p>
        <details><summary>학습 매개변수·판본 근거</summary>
          <pre>{JSON.stringify({ dataset_version: model.dataset_version, params: model.params, training_input: job.spec?.training_input, temporal_split: job.spec?.temporal_split }, null, 2)}</pre>
        </details>
      </>}
      {(job || error) && <button type="button" className="secondary" disabled={loading} onClick={() => void load()}>학습 기록 재조회</button>}
    </details>
  </section>;
}
