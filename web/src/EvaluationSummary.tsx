import type { EvaluationRevision } from "./types";

const kindLabels: Record<string, string> = {
  labeled: "레이블 기반 판정 평가 · 판정 정책을 정답 레이블과 비교했습니다.",
  ranking_only: "레이블 기반 순위 평가 · 판정 정책이 없어 판정 지표는 계산하지 않았습니다.",
  unlabeled: "레이블 없는 평가 · 정답 레이블이 없어 판정 건수와 비율만 제공합니다.",
};
const relationLabels: Record<string, string> = {
  same_scoring_result: "정책을 산정한 점수 결과와 동일 (독립 검증 아님)",
  distinct_calibration_test_time_ranges: "정책 산정 구간과 시간이 겹치지 않는 점수 결과",
  different_scoring_result_disjointness_unverified: "정책 산정과 다른 점수 결과 · 구간 비중복 미확인",
};

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function amount(value: unknown): string {
  const n = finite(value);
  return n === null ? "미정의" : n.toLocaleString("ko-KR", { maximumFractionDigits: 5 });
}
function count(value: unknown): string {
  const n = finite(value);
  return n === null ? "미정의" : `${n.toLocaleString("ko-KR")}건`;
}
function percent(value: unknown): string {
  const n = finite(value);
  return n === null ? "미정의" : `${(n * 100).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}%`;
}

export function labelCoverageLabel(coverage: string): string {
  return coverage === "full" ? "전체 행 레이블" : coverage === "none" ? "레이블 없음" : coverage;
}

/** 평가 판본의 판정 요약을 먼저 보이고, 레이블로만 정의되는 지표는 레이블이 있을 때만 보인다. */
export function EvaluationSummary({ evaluation }: { evaluation: EvaluationRevision }) {
  const metrics = evaluation.decision_metrics;
  const kind = String(metrics.evaluation_kind ?? "");
  const relation = typeof metrics.policy_reference_relation === "string"
    ? metrics.policy_reference_relation : null;
  const ranking = evaluation.ranking_metrics ?? {};
  const fields: Array<[string, string]> = [
    ["레이블", labelCoverageLabel(evaluation.label_coverage)],
    ["평가한 점수", count(metrics.score_count)],
    ["판정 임계값", metrics.threshold == null ? "정책 없음" : amount(metrics.threshold)],
  ];
  if (metrics.threshold != null) fields.push(
    ["이상 판정 건수", count(metrics.predicted_anomalies)],
    ["판정 비율", percent(metrics.alert_rate)],
  );
  if (kind === "labeled") fields.push(
    ["Precision", amount(metrics.precision)], ["Recall", amount(metrics.recall)],
    ["F1", amount(metrics.f1)],
    ["정탐 · 오탐", `${count(metrics.true_positives)} · ${count(metrics.false_positives)}`],
    ["미탐 · 정상", `${count(metrics.false_negatives)} · ${count(metrics.true_negatives)}`],
  );
  if (finite(ranking.roc_auc) !== null) fields.push(["ROC-AUC", amount(ranking.roc_auc)]);
  if (finite(ranking.average_precision) !== null) fields.push(
    ["Average Precision", amount(ranking.average_precision)]);
  if (relation) fields.push(["정책 기준 관계", relationLabels[relation] ?? relation]);
  return <>
    <p className="evaluation-kind">{kind === "unlabeled" && metrics.threshold == null
      ? "레이블 없는 평가 · 판정 정책이 없어 점수 요약만 제공합니다."
      : kindLabels[kind] ?? kind}</p>
    <dl className="ml-resource-fields">
      {fields.map(([label, value]) => <div className="ml-resource-field" key={label}>
        <dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  </>;
}
