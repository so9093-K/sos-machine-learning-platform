import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { formatRecordedAt } from "./display-format";
import type { EvaluationRevision, ModelEvaluationPage, ModelOfferingState, PublishedEvaluation } from "./types";

const percent = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%` : "—";
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? value.toLocaleString("ko-KR") : "—";
const decimal = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? value.toLocaleString("ko-KR", { maximumFractionDigits: 3 }) : "—";

/** 판정 기준을 검토량 관점의 문장으로 보인다. */
export function policyLabel(policy: PublishedEvaluation["policy"]): string {
  if (!policy) return "판정 기준 없음 · 순위 지표만";
  return policy.mode === "quantile" && typeof policy.alert_fraction === "number"
    ? `점수 상위 ${percent(policy.alert_fraction)} 검토` : `점수 ${decimal(policy.threshold)} 이상 검토`;
}

/** 공급자가 공개한 기준 데이터 평가. 검토량과 실제 포착을 함께 보여 기대치를 정하게 한다.
 *
 *  공개된 근거는 그 기준 데이터의 결과다. 사용자 데이터의 성능이나 승인으로 표현하지 않는다.
 */
export function PublishedEvaluations({ items }: { items: PublishedEvaluation[] }) {
  if (!items.length) return <p className="hint">공급자가 공개한 평가 근거가 없습니다. 정답 레이블이 있는 자기 데이터로 점수를
    계산한 뒤 판정 정책·평가를 실행해 확인할 수 있습니다.</p>;
  return <div className="published-evaluations">
    <p className="hint">공급자가 공개한 기준 데이터의 결과입니다. 데이터가 다르면 결과도 다르며, 자기 데이터의 판정 기준은
      자기 점수 분포로 정합니다. 검토 대상이 늘면 더 많이 잡지만 확인할 양도 늘어납니다.</p>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="공개 평가 근거 표">
      <table>
        <thead><tr>
          <th>기준 데이터</th><th>판정 기준</th><th>검토 대상</th><th>그중 실제 이상</th>
          <th>놓친 이상</th><th>정밀도</th><th>재현율</th><th>AP</th><th>ROC-AUC</th>
        </tr></thead>
        <tbody>{items.map(item => {
          const metrics = item.decision_metrics;
          return <tr key={item.evaluation_revision_id}>
            <td>{item.note}<small>평가 {formatRecordedAt(item.evaluated_at)}</small></td>
            <td>{policyLabel(item.policy)}</td>
            <td>{count(metrics.predicted_anomalies)}건<small>전체 {count(metrics.score_count)} 중 {percent(metrics.alert_rate)}</small></td>
            <td>{count(metrics.true_positives)}건</td>
            <td>{count(metrics.false_negatives)}건</td>
            <td>{percent(metrics.precision)}</td>
            <td>{percent(metrics.recall)}</td>
            <td>{decimal(item.ranking_metrics?.average_precision)}</td>
            <td>{decimal(item.ranking_metrics?.roc_auc)}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  </div>;
}

type Draft = Record<string, { selected: boolean; note: string }>;

/** 모델 소유 계정이 자기 평가 중 공개할 평가와 기준 데이터 설명을 정한다. */
export function PublishedEvaluationsEditor({ modelId, offering, onSaved }: {
  modelId: string; offering: ModelOfferingState; onSaved: (value: ModelOfferingState) => void;
}) {
  const [evaluations, setEvaluations] = useState<EvaluationRevision[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  useEffect(() => {
    let active = true;
    void request<ModelEvaluationPage>(`/api/ml/models/${encodeURIComponent(modelId)}/evaluations?limit=50`)
      .then(page => { if (active) setEvaluations(page.items); })
      .catch(cause => { if (active) setLoadError(formatError(cause)); });
    return () => { active = false; };
  }, [modelId]);
  useEffect(() => {
    setDraft(Object.fromEntries((offering.published_evaluations ?? []).map(item =>
      [item.evaluation_revision_id, { selected: true, note: item.note }])));
  }, [offering.published_evaluations]);
  const chosen = Object.entries(draft).filter(([, value]) => value.selected);
  const missingNote = chosen.some(([, value]) => !value.note.trim());
  async function save() {
    setSaving(true); setSaveError("");
    try {
      const value = await request<ModelOfferingState>(
        `/api/ml/models/${encodeURIComponent(modelId)}/offering/evaluations`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ evaluations: chosen.map(([id, value]) =>
            ({ evaluation_revision_id: id, note: value.note.trim() })) }),
        });
      onSaved(value);
    } catch (cause) { setSaveError(formatError(cause)); }
    finally { setSaving(false); }
  }
  if (loadError) return <p role="alert">평가 목록을 불러오지 못했습니다. {loadError}</p>;
  if (!evaluations) return <p role="status" className="hint">이 판본의 평가를 확인하는 중입니다.</p>;
  if (!evaluations.length) return <p className="hint">이 판본으로 만든 평가가 없습니다. 레이블이 있는 기준 데이터로 점수를 계산하고
    판정 정책·평가를 실행하면 여기서 공개할 수 있습니다.</p>;
  return <div className="published-evaluations-editor">
    <p className="hint">공개한 평가는 집계 수치와 아래 설명만 사용자에게 보입니다. 평가에 쓴 Dataset과 관측값은 공개하지 않습니다.
      설명에는 기준 데이터와 조건(예: 공개 데이터 이름·기간·레이블 정의)을 적습니다.</p>
    <ul className="public-source-parts">{evaluations.map(item => {
      const value = draft[item.id] ?? { selected: false, note: "" };
      const metrics = item.decision_metrics as Record<string, unknown>;
      return <li key={item.id}>
        <div>
          <label className="public-source-consent">
            <input type="checkbox" checked={value.selected}
              onChange={event => setDraft(current => ({ ...current, [item.id]: { ...value, selected: event.target.checked } }))} />
            평가 {formatRecordedAt(item.created_at)} · 레이블 {item.label_coverage === "full" ? "있음" : "없음"}
          </label>
          <p className="hint">판정 기준 점수 {decimal(metrics.threshold)} · 검토 대상 {percent(metrics.alert_rate)} · 정밀도 {percent(metrics.precision)} · 재현율 {percent(metrics.recall)}
            · AP {decimal((item.ranking_metrics as Record<string, unknown> | null)?.average_precision)}</p>
          {value.selected && <label>기준 데이터 설명
            <input value={value.note} maxLength={300} placeholder="예: CIC-IDS2017 정정판 수요일(DoS) · 레이블 0/1 · 공격 시도 제외"
              onChange={event => setDraft(current => ({ ...current, [item.id]: { ...value, note: event.target.value } }))} />
          </label>}
        </div>
      </li>;
    })}</ul>
    {saveError && <p role="alert">{saveError}</p>}
    <button type="button" disabled={saving || missingNote || chosen.length > 10} onClick={() => void save()}>
      {saving ? "공개 근거 저장 중…" : `공개 평가 근거 저장 (${chosen.length}개)`}</button>
    {missingNote && <p className="hint">공개할 평가마다 기준 데이터 설명이 필요합니다.</p>}
  </div>;
}
