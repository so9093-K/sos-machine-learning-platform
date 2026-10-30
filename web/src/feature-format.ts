/** 관측값과 학습 참조값은 실제 수치로 비교한다. 모델 기여도는 별도로 계산한다. */
export function formatFeatureValue(feature: string | undefined, value: number): string {
  const rate = feature === "io_bytes_per_sec" || feature === "net_bytes_per_sec";
  if (rate) {
    if (value !== 0 && Math.abs(value) < 0.01) return `${value.toExponential(3)} B/s`;
    const units = ["B/s", "kB/s", "MB/s", "GB/s", "TB/s"];
    const index = Math.min(units.length - 1,
      Math.max(0, Math.floor(Math.log10(Math.abs(value) || 1) / 3)));
    return `${(value / 1000 ** index).toLocaleString("ko-KR", { maximumFractionDigits: 2 })} ${units[index]}`;
  }
  return value !== 0 && Math.abs(value) < 0.000001
    ? value.toExponential(3) : value.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
}

