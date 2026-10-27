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
  get third() { return themeColor("--sos-chart-third"); },
  get positive() { return themeColor("--sos-chart-positive"); },
  get negative() { return themeColor("--sos-chart-negative"); },
  get threshold() { return themeColor("--sos-chart-threshold"); },
};

export function DataChart({ option, ariaLabel, onDataClick, selectedDataIndex }: {
  option: echarts.EChartsCoreOption;
  ariaLabel: string;
  onDataClick?: (dataIndex: number) => void;
  selectedDataIndex?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
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

      color: [CHART_COLORS.bar, CHART_COLORS.line, CHART_COLORS.third],
      textStyle: { color: text },
      categoryAxis: axis,
      valueAxis: axis,
      legend: { textStyle: { color: label } },
      tooltip: {
        backgroundColor: themeColor("--sos-panel"), borderColor: border,
        textStyle: { color: text },
      },
    });
    chartInstance.current = chart;
    // 개별 차트와 ECharts media의 baseOption 모두 동일한 기준선 대비를 적용한다.
    // Calibration 임계값과 SHAP의 0 기준선은 의미가 달라 색상도 구분한다.
    const themedSeries = (series: echarts.EChartsCoreOption["series"]) =>
      Array.isArray(series) ? series.map(item => {
        if (!item || typeof item !== "object" || !("markLine" in item)) return item;
        const line = item.markLine as {
          data?: Array<{ xAxis?: number; yAxis?: number }>;
          lineStyle?: Record<string, unknown>;
        } | undefined;
        if (!line) return item;
        const zeroBaseline = line.data?.some(entry => entry.xAxis === 0 || entry.yAxis === 0);
        return { ...item, markLine: { ...line, lineStyle: {
          color: zeroBaseline ? border : CHART_COLORS.threshold,
          width: zeroBaseline ? 1.5 : 2, type: "dashed",
          ...line.lineStyle,
        } } };
      }) : series;
    if (option.baseOption && typeof option.baseOption === "object") {
      const base = option.baseOption as echarts.EChartsCoreOption;
      chart.setOption({
        ...option, baseOption: { ...base, series: themedSeries(base.series) },
      });
    } else {
      chart.setOption({ ...option, series: themedSeries(option.series) });
    }
    chart.on("click", params => {
      if (typeof params.dataIndex === "number" && Number.isInteger(params.dataIndex)) {
        clickHandler.current?.(params.dataIndex);
      }
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    // 접힌 상세에 미리 생성된 차트는 펼친 직후 실제 레이아웃으로 다시 계산한다.
    const parentDetails = ref.current.closest("details");
    const resizeOnToggle = () => {
      if (parentDetails?.open) requestAnimationFrame(() => chart.resize());
    };
    parentDetails?.addEventListener("toggle", resizeOnToggle);
    return () => {
      parentDetails?.removeEventListener("toggle", resizeOnToggle);
      observer.disconnect();
      chartInstance.current = null;
      chart.dispose();
    };
  }, [option]);
  useEffect(() => {
    if (chartInstance.current && typeof selectedDataIndex === "number" &&
        Number.isInteger(selectedDataIndex) && selectedDataIndex >= 0) {
      chartInstance.current.dispatchAction({
        type: "select", seriesIndex: 0, dataIndex: selectedDataIndex,
      });
    }
  }, [selectedDataIndex, option]);
  return <div ref={ref} className="chart" role="img" aria-label={ariaLabel} />;
}
