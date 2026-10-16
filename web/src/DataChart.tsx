/** EDA와 Discovery가 사용하는 단일 차트 어댑터·팔레트. */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export const CHART_COLORS = {
  text: "#cbd5e1",
  label: "#94a3b8",
  grid: "#334155",
  bar: "#7d92e7",
  line: "#69b9ff",
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
