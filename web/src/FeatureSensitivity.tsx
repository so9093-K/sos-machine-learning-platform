import { CHART_COLORS, DataChart } from "./DataChart";
import { FeatureResultDetails } from "./FeatureResultDetails";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { escapeShapTooltipText } from "./ShapExplanation";
import type { ExplanationResult } from "./types";
const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

export const formatSensitivityValue = (value: number) =>
  value !== 0 && Math.abs(value) < 1e-8 ? value.toExponential(3)
    : value.toLocaleString("ko-KR", { maximumFractionDigits: 8 });

export function SensitivityChart({ result }: { result: ExplanationResult }) {
  const raw = result.output.feature_sensitivities;
  const entries = (Array.isArray(raw) ? raw as Array<Record<string, unknown>> : [])
    .filter(entry => typeof entry.feature === "string" && numeric(entry.anomaly_score_delta) !== null)
    .sort((a, b) => Math.abs(Number(b.anomaly_score_delta)) - Math.abs(Number(a.anomaly_score_delta)));
  const ranked = entries.filter(entry => entry.anomaly_score_delta !== 0).slice(0, 5);
  if (!entries.length) return <p>점수 민감도 근거가 없습니다.</p>;
  const shown = [...ranked].reverse();
  const option = { baseOption: {
    aria: { enabled: true },
    tooltip: { trigger: "axis", formatter: (params: Array<{ dataIndex: number }>) => {
      const item = shown[params[0]?.dataIndex ?? -1];
      return item ? `${escapeShapTooltipText(activityFeatureLabel(String(item.feature)))}<br/>점수 차이 ${formatSensitivityValue(Number(item.anomaly_score_delta))}` : "";
    } },
    grid: { left: 210, right: 35, top: 18, bottom: 40 },
    xAxis: { type: "value" as const, name: "점수 차이", nameLocation: "middle" as const, nameGap: 28,
      axisLabel: { formatter: (value: number) => value !== 0 && Math.abs(value) < 0.001
        ? value.toExponential(1) : value.toFixed(3) } },
    yAxis: { type: "category" as const, data: shown.map(x => activityFeatureLabel(String(x.feature))),
      axisLabel: { width: 180, overflow: "truncate" as const, fontSize: 12 } },
    series: [{ type: "bar" as const, name: "점수 차이",
      data: shown.map(x => Number(x.anomaly_score_delta)),
      itemStyle: { color: (params: { value: number }) =>
        params.value >= 0 ? CHART_COLORS.positive : CHART_COLORS.negative },
      markLine: { silent: true, symbol: "none", label: { show: false }, data: [{ xAxis: 0 }] } }],
  }, media: [{ query: { maxWidth: 620 }, option: {
    grid: { left: 135, right: 25, top: 18, bottom: 48 },
    yAxis: { axisLabel: { width: 110, overflow: "truncate" as const, fontSize: 11 } },
  } }] };
  const number = (value: unknown) => numeric(value) === null ? "결측" : formatSensitivityValue(Number(value));
  return <div className="sensitivity-result">
    {ranked.length ? <>
      <h6>점수 변화가 큰 항목 {ranked.length}개</h6>
      <DataChart option={option} ariaLabel="점수 변화의 절댓값이 큰 상위 5개 항목의 치환 민감도" />
      <p className="hint">각 항목을 학습 중앙값으로 바꿨을 때의 점수 차이입니다. 양수는 점수가 낮아진 경우이며 인과적 기여도는 아닙니다.</p>
    </> : <div className="shap-zero" role="status">{entries.every(item => numeric(item.actual_value) !== null && item.actual_value === item.reference_median)
      ? <p>입력값이 이미 학습 중앙값과 같습니다. 이번 치환에서는 실제 입력값이 달라지지 않았습니다.</p>
      : entries.some(item => numeric(item.actual_value) !== null && numeric(item.reference_median) !== null && item.actual_value !== item.reference_median)
        ? <p>기준값과 다른 입력을 각각 바꿔도 이번 비교에서 점수가 달라지지 않았습니다. 이 결과만으로 항목의 전반적인 중요도를 판단할 수는 없습니다.</p>
        : <p>이번 계산에서 점수 차이는 모두 0입니다. 치환 전 입력을 확인할 근거가 없어 입력이 실제로 달라졌는지는 판단할 수 없습니다.</p>}</div>}
    <FeatureResultDetails key={result.id} summary={`개별 값 확인 · ${entries.length}개 항목`}
      columns={["관측값", "학습 중앙값", "치환 후 점수", "점수 차이"]}
      rows={entries.map(item => ({ feature: String(item.feature), values: [
        item.missing_imputed ? "결측값 대체" : number(item.actual_value), number(item.reference_median),
        number(item.score_if_replaced), number(item.anomaly_score_delta),
      ] }))} />
  </div>;
}
