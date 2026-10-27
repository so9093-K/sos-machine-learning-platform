/** 단일 Feature의 입력·관측값과 Train 중앙값을 비교한다.
 * 막대는 동일 Feature 안에서만 비례하며 정상 범위 또는 모델 기여도가 아니다.
 */
export function FeatureValueComparison({ actual, reference, actualLabel = "관측값" }: {
  actual: number | null | undefined;
  reference: number | null | undefined;
  actualLabel?: string;
}) {
  const valid = (value: number | null | undefined): value is number =>
    typeof value === "number" && Number.isFinite(value);
  const actualReady = valid(actual);
  const referenceReady = valid(reference);
  const max = actualReady && referenceReady && actual >= 0 && reference >= 0
    ? Math.max(actual, reference) : 0;
  const showBars = max > 0;
  const display = (value: number | null | undefined) => valid(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 6 }) : "비교 기준 없음";
  return <div className="feature-reference-comparison">
    <div className="feature-reference-values">
      <div><span>{actualLabel}</span><strong>{actualReady ? display(actual) : "유효한 값 없음"}</strong></div>
      <div><span>Train 중앙값</span><strong>{display(reference)}</strong></div>
    </div>
    {showBars && <div className="feature-reference-bars" aria-hidden="true">
      <span><i style={{ width: `${actual! / max * 100}%` }} /></span>
      <span><i style={{ width: `${reference! / max * 100}%` }} /></span>
    </div>}
  </div>;
}
