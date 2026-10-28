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
  const showBars = max > 0 && referenceReady && reference > 0;
  // 비율이 극단적이면 짧은 막대가 보이지 않으므로 수치 비교를 우선 안내한다.
  const tooDifferentForBars = showBars && actualReady &&
    Math.min(actual!, reference!) / max < 0.01;
  const display = (value: number | null | undefined) => {
    if (!valid(value)) return "비교 기준 없음";
    // 표시 반올림으로 0과 혼동될 수 있는 작은 실제값은 지수 표기를 사용한다.
    return value !== 0 && Math.abs(value) < 0.000001
      ? value.toExponential(3)
      : value.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
  };
  return <div className="feature-reference-comparison">
    <div className="feature-reference-values">
      <div><span><i className="feature-reference-legend-actual" aria-hidden="true" />{actualLabel}</span>
        <strong>{actualReady ? display(actual) : "유효한 값 없음"}</strong></div>
      <div><span><i className="feature-reference-legend-train" aria-hidden="true" />Train 중앙값</span>
        <strong>{display(reference)}</strong></div>
    </div>
    {showBars && <div className="feature-reference-bars" aria-hidden="true">
      <span><i style={{ width: `${actual! / max * 100}%` }} /></span>
      <span><i style={{ width: `${reference! / max * 100}%` }} /></span>
    </div>}
    {tooDifferentForBars && <small className="feature-reference-scale-note">
      값 차이가 커 작은 값의 막대가 거의 보이지 않습니다. 위 숫자로 비교하세요.
    </small>}
  </div>;
}
