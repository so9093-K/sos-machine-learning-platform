/** EDA와 Discovery가 사용하는 단일 차트 어댑터·팔레트. */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export const CHART_COLORS = {
  text: "#f3f0eb",
  label: "#c3bab1",
  grid: "#5d5651",
  bar: "#9ec6f2",
  line: "#b6e9c3",
} as const;

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
