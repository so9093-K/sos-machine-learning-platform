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
};

export function DataChart({ option, ariaLabel }: {
  option: echarts.EChartsCoreOption;
  ariaLabel: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={ref} className="chart" role="img" aria-label={ariaLabel} />;
}
