/** 분석 결과의 범위·선택 방식·표시 한계를 사용자에게 일관되게 전달한다. */
export type EvidenceDetail = { label: string; value: string };

export function analysisScopeLabel(scope: string): string {
  if (scope === "full") return "전체 데이터 분석";
  if (scope === "sample") return "부분 데이터 분석";
  return "분석 범위 미확인";
}

export function dataSelectionLabel(method: string): string {
  switch (method) {
    case "reservoir": return "무작위 추출 (Reservoir)";
    case "full_scan": return "전체 행 대상";
    case "limited_rows": return "제한 조회 (정렬 기준 없음)";
    case "none": return "선택 방식 없음";
    default: return "선택 방식 미확인";
  }
}

/** 기술 정보는 필요할 때 펼치되, 중요한 결과 해석의 한계는 기본 화면에 남긴다. */
export function AnalysisEvidence({ summary, details, note }: {
  summary: string;
  details: readonly EvidenceDetail[];
  note?: string;
}) {
  return <div className="analysis-evidence" aria-label="분석 근거">
    <p className="analysis-evidence-summary">{summary}</p>
    {note && <p className="analysis-evidence-note">{note}</p>}
    <details>
      <summary>분석 근거 자세히</summary>
      <dl>{details.map(item => <div key={item.label}>
        <dt>{item.label}</dt><dd>{item.value}</dd>
      </div>)}</dl>
    </details>
  </div>;
}
