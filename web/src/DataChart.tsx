/** EDA와 Discovery가 사용하는 단일 차트 어댑터·팔레트. */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";

/** CSS 토큰을 차트의 단일 색상 정본으로 사용한다. Canvas에는 계산된 값이 필요하다. */
function themeColor(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const CHART_COLORS = {
  get text() { return themeColor("--sos-text"); },
  get label() { return themeColor("--sos-muted"); },
  get grid() { return themeColor("--sos-border"); },
  get bar() { return themeColor("--sos-chart-bar"); },
  get line() { return themeColor("--sos-chart-line"); },
  get positive() { return themeColor("--sos-chart-positive"); },
  get negative() { return themeColor("--sos-chart-negative"); },
  get threshold() { return themeColor("--sos-chart-threshold"); },
};

export function DataChart({ option, ariaLabel, onDataClick }: {
  option: echarts.EChartsCoreOption;
  ariaLabel: string;
  onDataClick?: (dataIndex: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const clickHandler = useRef(onDataClick);
  clickHandler.current = onDataClick;
  useEffect(() => {
    if (!ref.current) return;
    // Canvas는 CSS 변수 자체를 해석하지 못하므로 현재 토큰을 ECharts 테마로 전달한다.
    const text = CHART_COLORS.text;
    const label = themeColor("--sos-chart-label");
    const grid = themeColor("--sos-chart-grid");
    const border = themeColor("--sos-chart-grid-strong");
    const axis = {
      axisLine: { lineStyle: { color: border } },
      axisTick: { lineStyle: { color: border } },
      axisLabel: { color: label, fontSize: 12 },
      nameTextStyle: { color: label, fontSize: 12 },
      splitLine: { lineStyle: { color: grid } },
    };
    const chart = echarts.init(ref.current, {
      color: [CHART_COLORS.bar, CHART_COLORS.line, CHART_COLORS.positive],
      textStyle: { color: text },
      categoryAxis: axis,
      valueAxis: axis,
      legend: { textStyle: { color: label } },
      tooltip: {
        backgroundColor: themeColor("--sos-panel"), borderColor: border,
        textStyle: { color: text },
      },
    });
    // 기존 차트 옵션은 유지하고, 공통 임계선에만 테마 대비를 적용한다.
    const themedSeries = Array.isArray(option.series) ? option.series.map(series => {
      if (!series || typeof series !== "object" || !("markLine" in series)) return series;
      const current = series.markLine as {
        lineStyle?: Record<string, unknown>;
      } | undefined;
      if (!current) return series;
      return { ...series, markLine: {
        ...current,
        lineStyle: { color: CHART_COLORS.threshold, width: 2, type: "dashed",
          ...current.lineStyle },
      } };
    }) : option.series;
    chart.setOption({ ...option, series: themedSeries });
    chart.on("click", params => {
      if (typeof params.dataIndex === "number" && Number.isInteger(params.dataIndex)) {
        clickHandler.current?.(params.dataIndex);
      }
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={ref} className="chart" role="img" aria-label={ariaLabel} />;
}
