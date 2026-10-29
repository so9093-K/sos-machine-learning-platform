import { useEffect, useId, useState } from "react";
import { ApiError, formatError, request } from "./api";
import { EvaluationSummary } from "./EvaluationSummary";
import { formatRecordedAt, policyModeLabel, scoreDirectionLabel } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type { DecisionPolicyRevision, EvaluationRevision, ModelEvaluationPage, ScoringResult } from "./types";

type Failure = { message: string; restricted: boolean };
function failure(cause: unknown): Failure {
  return { message: formatError(cause), restricted: cause instanceof ApiError && [403, 404].includes(cause.status) };
}

function EvidenceFailure({ error, onRetry }: { error: Failure; onRetry: () => void }) {
  return <div role="alert">
    <p>{error.restricted ? "평가 근거 접근 불가" : "평가 근거 조회 실패"} · {error.message}</p>
    <p className="hint">{error.restricted
      ? "이 계정의 열람 권한 또는 결과의 현재 상태를 확인하세요. 모델 제공은 공급자의 비공개 평가를 공개하지 않습니다."
      : "평가 부재로 판단할 수 없습니다. 다시 조회해 주세요."}</p>
    <button type="button" className="secondary" onClick={onRetry}>평가 근거 다시 조회</button>
  </div>;
}

/** 평가·점수·정책의 불변 참조를 대조한 뒤 실제 조건과 지표만 표시한다. */
function EvaluationEvidenceDetails({ modelId, evaluation, onOpenResource }: {
  modelId: string; evaluation: EvaluationRevision;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    scoring: ScoringResult; policy: DecisionPolicyRevision | null;
  } | null>(null);
  const [error, setError] = useState<Failure | null>(null);
  useEffect(() => {
    let active = true;
    setState(null); setError(null);
    void Promise.all([
      request<ScoringResult>(`/api/ml/scoring/results/${encodeURIComponent(evaluation.scoring_result_id)}`),
      evaluation.policy_revision_id
        ? request<DecisionPolicyRevision>(`/api/ml/policies/${encodeURIComponent(evaluation.policy_revision_id)}`)
        : Promise.resolve(null),
    ]).then(([scoring, policy]) => {
      if (scoring.id !== evaluation.scoring_result_id || scoring.model_revision_id !== modelId ||
        (policy && policy.id !== evaluation.policy_revision_id)) {
        throw new Error("평가·점수·모델의 판본 참조가 일치하지 않습니다. 근거를 표시하지 않습니다.");
      }
      if (active) setState({ scoring, policy });
    }).catch(cause => { if (active) setError(failure(cause)); });
    return () => { active = false; };
  }, [modelId, evaluation.id, revision]);
  if (error) return <EvidenceFailure error={error} onRetry={() => setRevision(value => value + 1)} />;
  if (!state) return <p role="status">평가의 모델·입력·정책 참조 확인 중…</p>;
  const { scoring, policy } = state;
  const scope = scoring.score_summary.temporal_scope as Record<string, unknown> | undefined;
  const start = scope?.partition === "test" ? scope.calibration_end : scope?.partition === "calibration" ? scope.train_end : null;
  const end = scope?.partition === "calibration" ? scope.calibration_end : null;
  return <div className="model-evaluation-detail">
    <h4>{scope?.partition === "test" ? "Test 평가 결과" : "점수 결과 평가"}</h4>
    <p className="hint">평가 {formatRecordedAt(evaluation.created_at)} · 기록된 평가이며 사용자 데이터의 성능 보장은 아닙니다.</p>
    <EvaluationSummary evaluation={evaluation} />
    <div className="ml-resource-actions">
      <button type="button" className="secondary" onClick={() => onOpenResource("evaluation", evaluation.id)}>평가 상세 →</button>
      <button type="button" className="secondary" onClick={() => onOpenResource("scoring", scoring.id)}>
        {scope?.partition === "test" ? "Test 점수 결과 →" : "평가한 점수 결과 →"}</button>
      {policy && <button type="button" className="secondary" onClick={() => onOpenResource("policy", policy.id)}>판정 정책 →</button>}
    </div>
    <details>
      <summary>평가 입력·점수·정책 조건</summary>
      <p>Dataset 판본 · {scoring.dataset_id} · {scoring.dataset_version}</p>
      <p>점수화 범위 · {scoring.row_count.toLocaleString("ko-KR")}행 · {scoreDirectionLabel(scoring.score_direction)}</p>
      {scope ? <p>기록된 시간 구간 · {String(scope.partition ?? "미확인")} · 시간 컬럼 {String(scope.timestamp_column ?? "미확인")}
        {typeof start === "string" && ` · ${formatRecordedAt(start)} 이상`}
        {typeof end === "string" && ` · ${formatRecordedAt(end)} 미만`}</p>
        : <p>시간 분할 근거 없음 · 별도 Test 구간이나 통계적 독립성을 가정하지 않습니다.</p>}
      {scoring.score_summary.training_data_reused === true && <p>학습에 사용한 동일 Dataset 판본의 점수입니다. 독립 Test 평가가 아닙니다.</p>}
      <p>정답 레이블 컬럼 · {scoring.label_column ?? "없음"}</p>
      <p>{policy ? `판정 정책 · ${policyModeLabel(policy.mode)} · 임계값 ${policy.threshold.toLocaleString("ko-KR", { maximumSignificantDigits: 6 })}`
        : "판정 정책 없음 · 이상 여부를 확정하는 임계값은 제공하지 않습니다."}</p>
      {policy && scoring.score_direction === "higher_is_more_anomalous" &&
        <p>이상 점수가 임계값 이상이면 이상 후보로 판정합니다. 사건의 확정 판정은 아닙니다.</p>}
    </details>
    <p className="hint">이상 점수는 사건 발생 확률이 아닙니다. 레이블 없는 판정 건수·비율은 탐지 정확도가 아니며,
      시간 구간 분리만으로 통계적 독립성이나 업무 적합성이 입증되지는 않습니다.</p>
  </div>;
}

/** 목록 진입·새로고침에서도 모델의 기존 평가를 소유권 범위 안에서 조회한다. */
export function ModelEvaluationEvidence({ modelId, onOpenResource, supplierEvidenceVisible }: {
  modelId: string; onOpenResource: (kind: MLResourceKind, id: string) => void;
  supplierEvidenceVisible: boolean | null;
}) {
  const selectId = useId();
  const [cursor, setCursor] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [state, setState] = useState<{ key: string; page: ModelEvaluationPage | null; error: Failure | null } | null>(null);
  const key = `${modelId}:${cursor ?? ""}:${revision}`;
  useEffect(() => {
    let active = true;
    void request<ModelEvaluationPage>(`/api/ml/models/${encodeURIComponent(modelId)}/evaluations?limit=5` +
      (cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""))
      .then(page => { if (active) setState({ key, page, error: null }); })
      .catch(cause => { if (active) setState({ key, page: null, error: failure(cause) }); });
    return () => { active = false; };
  }, [key]);
  const current = state?.key === key ? state : null;
  const page = current?.page;
  const selected = page?.items.find(item => item.id === selectedId) ?? page?.items[0];
  return <section className="model-evaluation-evidence" aria-label="모델 평가 근거">
    <h3>평가 근거</h3>
    {supplierEvidenceVisible === false && <p className="hint" role="status">
      공급자의 평가 근거는 이 계정에 비공개입니다. 이 계정이 생성한 평가만 조회합니다.</p>}
    {!current && <p role="status">모델의 평가 근거 확인 중…</p>}
    {current?.error && <EvidenceFailure error={current.error} onRetry={() => setRevision(value => value + 1)} />}
    {page && <>
      <p className="hint">열람 가능한 평가를 최신순으로 5건씩 조회합니다. 평가별 데이터와 정책 조건을 확인하세요.</p>
      {page.items.length === 0 && <p>{cursor ? "이 페이지에서 확인된 평가 근거가 없습니다."
        : supplierEvidenceVisible === false ? "이 계정이 생성한 평가가 없습니다. 공급자의 평가 부재를 뜻하지 않습니다."
          : "이 계정에서 확인된 평가 근거가 없습니다. 학습·Registry 등록만으로 성능을 판단할 수 없습니다."}</p>}
      {page.items.length > 1 && <div className="model-evaluation-picker"><label htmlFor={selectId}>평가 선택</label>
        <select id={selectId} value={selected?.id ?? ""} onChange={event => setSelectedId(event.target.value)}>
        {page.items.map(item => <option key={item.id} value={item.id}>{formatRecordedAt(item.created_at)} · {item.id.slice(0, 8)}</option>)}
      </select></div>}
      {selected && <EvaluationEvidenceDetails key={selected.id} modelId={modelId} evaluation={selected} onOpenResource={onOpenResource} />}
      {(cursor || page.next_cursor) && <div className="ml-resource-actions">
        {cursor && <button type="button" className="secondary" onClick={() => { setCursor(null); setSelectedId(""); }}>최근 평가</button>}
        {page.next_cursor && <button type="button" className="secondary" onClick={() => { setCursor(page.next_cursor!); setSelectedId(""); }}>이전 평가 5건</button>}
      </div>}
    </>}
  </section>;
}
