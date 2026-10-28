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

export function FeatureValueComparison({ actual, reference, actualLabel = "관측값", feature }: {
  actual: number | null | undefined;
  reference: number | null | undefined;
  actualLabel?: string;
  feature?: string;
}) {
  const valid = (value: number | null | undefined): value is number =>
    typeof value === "number" && Number.isFinite(value);
  const actualReady = valid(actual);
  const referenceReady = valid(reference);
  const raw = (value: number | null | undefined) => valid(value) ? String(value) : undefined;
  let comparison = "비교 불가";
  if (actualReady && referenceReady) {
    const ratio = reference > 0 && actual >= 0 ? actual / reference : null;
    const difference = actual - reference;
    if (actual === reference) comparison = "같음";
    else if (ratio !== null && Number.isFinite(ratio)) {
      comparison = `약 ${ratio !== 0 && ratio < 0.01 ? ratio.toExponential(2)
        : ratio.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}배`;
    } else if (Number.isFinite(difference)) {
      comparison = `${difference > 0 ? "+" : ""}${formatFeatureValue(feature, difference)}`;
    }
  }
  return <div className="feature-reference-comparison">
    <div className="feature-reference-values">
      <div><span>{actualLabel}</span><strong title={raw(actual)}>
        {actualReady ? formatFeatureValue(feature, actual) : "유효한 값 없음"}</strong></div>
      <div><span>학습 중앙값</span><strong title={raw(reference)}>
        {referenceReady ? formatFeatureValue(feature, reference) : "비교 기준 없음"}</strong></div>
      <div><span>참조값 대비</span><strong>{comparison}</strong></div>
    </div>
  </div>;
}
